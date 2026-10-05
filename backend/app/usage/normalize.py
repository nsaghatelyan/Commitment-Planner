"""Normalize billing-export files (FOCUS 1.0, AWS CUR 2.0) into USAGE_SCHEMA with DuckDB."""

import re

import duckdb
import pyarrow as pa

from app.usage.schema import USAGE_SCHEMA, conform

MAP_TYPE = "MAP(VARCHAR, VARCHAR)"


def detect_format(table: pa.Table) -> str:
    cols = set(table.column_names)
    if "ChargePeriodStart" in cols:
        return "focus"
    if "line_item_usage_start_date" in cols:
        return "cur2"
    raise ValueError(f"Unrecognized billing export format; columns: {sorted(cols)[:20]}")


def normalize(table: pa.Table, provider: str) -> pa.Table:
    if detect_format(table) == "focus":
        return normalize_focus(table, provider)
    return normalize_cur2(table)


class _Cols:
    """Builds SQL expressions that tolerate columns missing from a given export version."""

    def __init__(self, table: pa.Table) -> None:
        self.table = table
        self.names = set(table.column_names)

    def col(self, *candidates: str, default: str = "NULL") -> str:
        for name in candidates:
            if name in self.names:
                return f'"{name}"'
        return default

    def text(self, *candidates: str) -> str:
        return f"NULLIF(CAST({self.col(*candidates)} AS VARCHAR), '')"

    def num(self, *candidates: str) -> str:
        return f"CAST({self.col(*candidates)} AS DOUBLE)"

    def ts(self, *candidates: str) -> str:
        return f"CAST({self.col(*candidates)} AS TIMESTAMPTZ)"

    def map_or_json(self, *candidates: str) -> str:
        for name in candidates:
            if name not in self.names:
                continue
            if pa.types.is_map(self.table.schema.field(name).type):
                return f'CAST("{name}" AS {MAP_TYPE})'
            return f"""json_transform("{name}", '"{MAP_TYPE}"')"""
        return f"CAST(NULL AS {MAP_TYPE})"

    def json_field(self, column: str, *keys: str) -> str:
        """First non-null key from a JSON string (or map) column."""
        if column not in self.names:
            return "NULL"
        if pa.types.is_map(self.table.schema.field(column).type):
            parts = [f"element_at(\"{column}\", '{k}')[1]" for k in keys]
        else:
            parts = [f"json_extract_string(\"{column}\", '$.{k}')" for k in keys]
        return f"COALESCE({', '.join(parts)})"


def _run(table: pa.Table, select_sql: str) -> pa.Table:
    con = duckdb.connect()
    con.register("src", table)
    con.execute("SET TimeZone = 'UTC'")
    return _with_derived(conform(con.execute(select_sql).to_arrow_table()))


def normalize_focus(table: pa.Table, provider: str) -> pa.Table:
    """FOCUS 1.0 (AWS Data Exports, Azure Cost Management exports). Also accepts FOCUS 0.5 names."""
    c = _Cols(table)
    # AWS FOCUS carries x_UsageType ("USE1-BoxUsage:m5.large"); Azure carries x_SkuDetails JSON.
    instance_type = (
        f"COALESCE(NULLIF(split_part({c.text('x_UsageType')}, ':', 2), ''), "
        f"{c.json_field('x_SkuDetails', 'VMSize', 'ServiceType', 'InstanceType')}, "
        f"{c.json_field('SkuPriceDetails', 'InstanceType', 'VMSize')})"
    )
    category = c.text("PricingCategory")
    sql = f"""
    SELECT
        {c.ts("ChargePeriodStart")} AS charge_period_start,
        {c.ts("ChargePeriodEnd")} AS charge_period_end,
        '{provider}' AS provider,
        {c.text("BillingAccountId")} AS billing_account_id,
        {c.text("SubAccountId")} AS sub_account_id,
        {c.text("SubAccountName")} AS sub_account_name,
        lower({c.text("RegionId", "Region")}) AS region,
        {c.text("ServiceCategory")} AS service_category,
        {c.text("ServiceName")} AS service_name,
        {c.text("SkuId")} AS sku_id,
        {c.text("ResourceId")} AS resource_id,
        {c.text("ResourceName")} AS resource_name,
        NULL AS instance_family,
        {instance_type} AS instance_type,
        COALESCE({c.text("x_OperatingSystem")},
                 {c.json_field("x_SkuDetails", "OperatingSystem", "ImageType")}) AS operating_system,
        {c.text("x_Tenancy")} AS tenancy,
        COALESCE({c.text("x_DatabaseEngine")},
                 {c.json_field("SkuPriceDetails", "DatabaseEngine")}) AS database_engine,
        COALESCE({c.text("x_DeploymentOption")},
                 {c.json_field("SkuPriceDetails", "DeploymentOption")}) AS deployment_option,
        {c.text("ChargeCategory")} AS charge_category,
        CASE
            WHEN {category} IN ('Committed', 'Commitment-Based') THEN 'Commitment-Based'
            WHEN {category} IN ('Dynamic', 'Spot') THEN 'Spot'
            WHEN {category} IS NULL THEN NULL
            ELSE 'On-Demand'
        END AS pricing_category,
        {c.text("CommitmentDiscountId")} AS commitment_id,
        {c.text("CommitmentDiscountType")} AS commitment_type,
        {c.text("CommitmentDiscountStatus")} AS commitment_status,
        {c.num("ConsumedQuantity", "UsageQuantity")} AS usage_quantity,
        {c.text("ConsumedUnit", "UsageUnit", "PricingUnit")} AS usage_unit,
        {c.num("x_UsageNormalizedQuantity", "x_NormalizedUsageQuantity")} AS normalized_units,
        {c.num("ListCost")} AS list_cost,
        {c.num("BilledCost")} AS billed_cost,
        {c.num("EffectiveCost", "AmortizedCost")} AS effective_cost,
        CASE WHEN COALESCE({c.text("ChargeCategory")}, 'Usage') = 'Usage'
             AND COALESCE({c.text("CommitmentDiscountStatus")}, 'Used') = 'Used'
            THEN {c.num("ListCost")} END AS on_demand_equiv_cost,
        {c.map_or_json("Tags")} AS tags
    FROM src
    WHERE {c.col("ChargePeriodStart")} IS NOT NULL
    """
    return _run(table, sql)


def normalize_cur2(table: pa.Table) -> pa.Table:
    """AWS CUR 2.0. SavingsPlanNegation lines are dropped and covered usage gets billed_cost=0;
    each recurring commitment fee becomes a Purchase row plus an Unused row."""
    c = _Cols(table)
    has_product_map = "product" in c.names

    def prod(flat: str, key: str) -> str:
        nested = f"element_at(product, '{key}')[1]" if has_product_map else "NULL"
        return f"COALESCE({c.text(flat)}, {nested})"

    li_type = c.text("line_item_line_item_type")
    unblended = c.num("line_item_unblended_cost")
    sp_arn = c.text("savings_plan_savings_plan_a_r_n")
    ri_arn = c.text("reservation_reservation_a_r_n")
    usage_type = c.text("line_item_usage_type")
    base = f"""
    WITH base AS (
        SELECT
            {li_type} AS li_type,
            {c.ts("line_item_usage_start_date")} AS charge_period_start,
            {c.ts("line_item_usage_end_date")} AS charge_period_end,
            {c.text("bill_payer_account_id")} AS billing_account_id,
            {c.text("line_item_usage_account_id")} AS sub_account_id,
            {c.text("line_item_usage_account_name")} AS sub_account_name,
            {prod("product_region_code", "region")} AS region,
            {prod("product_product_family", "product_family")} AS service_category,
            {c.text("line_item_product_code", "product_servicecode")} AS service_name,
            {c.text("product_sku")} AS sku_id,
            {c.text("line_item_resource_id")} AS resource_id,
            {prod("product_instance_family", "instance_family")} AS instance_family,
            {prod("product_instance_type", "instance_type")} AS instance_type,
            {prod("product_operating_system", "operating_system")} AS operating_system,
            {prod("product_tenancy", "tenancy")} AS tenancy,
            {prod("product_database_engine", "database_engine")} AS database_engine,
            {prod("product_deployment_option", "deployment_option")} AS deployment_option,
            CASE WHEN {c.text("pricing_term")} = 'Spot' OR {usage_type} LIKE '%SpotUsage%'
                THEN TRUE ELSE FALSE END AS is_spot,
            COALESCE({sp_arn}, {ri_arn}) AS commitment_id,
            CASE WHEN {sp_arn} IS NOT NULL THEN 'Savings Plan'
                 WHEN {ri_arn} IS NOT NULL THEN 'Reservation' END AS commitment_type,
            {c.num("line_item_usage_amount")} AS usage_quantity,
            {c.text("pricing_unit")} AS usage_unit,
            {c.num("line_item_normalized_usage_amount")} AS normalized_units,
            {c.num("pricing_public_on_demand_cost")} AS list_cost,
            {unblended} AS unblended,
            {c.num("savings_plan_savings_plan_effective_cost")} AS sp_effective,
            {c.num("reservation_effective_cost")} AS ri_effective,
            {c.num("savings_plan_total_commitment_to_date")}
                - {c.num("savings_plan_used_commitment")} AS sp_unused,
            COALESCE({c.num("reservation_unused_amortized_upfront_fee_for_billing_period")}, 0)
                + COALESCE({c.num("reservation_unused_recurring_fee")}, 0) AS ri_unused,
            {c.map_or_json("resource_tags")} AS tags
        FROM src
        WHERE {c.col("line_item_usage_start_date")} IS NOT NULL
            AND COALESCE({li_type}, '') <> 'SavingsPlanNegation'
    )
    """
    common = """
        charge_period_start, charge_period_end, 'aws' AS provider, billing_account_id,
        sub_account_id, sub_account_name, region, service_category, service_name, sku_id,
        resource_id, NULL AS resource_name, instance_family, instance_type, operating_system,
        tenancy, database_engine, deployment_option
    """
    sql = f"""{base}
    SELECT {common},
        CASE li_type WHEN 'Tax' THEN 'Tax'
            WHEN 'Credit' THEN 'Credit' WHEN 'Refund' THEN 'Credit'
            WHEN 'Fee' THEN 'Purchase' WHEN 'SavingsPlanUpfrontFee' THEN 'Purchase'
            WHEN 'Usage' THEN 'Usage' WHEN 'DiscountedUsage' THEN 'Usage'
            WHEN 'SavingsPlanCoveredUsage' THEN 'Usage'
            ELSE 'Adjustment' END AS charge_category,
        CASE WHEN is_spot THEN 'Spot'
             WHEN li_type IN ('SavingsPlanCoveredUsage', 'DiscountedUsage') THEN 'Commitment-Based'
             ELSE 'On-Demand' END AS pricing_category,
        commitment_id, commitment_type,
        CASE WHEN li_type IN ('SavingsPlanCoveredUsage', 'DiscountedUsage') THEN 'Used'
            END AS commitment_status,
        usage_quantity, usage_unit, normalized_units, list_cost,
        CASE WHEN li_type IN ('SavingsPlanCoveredUsage', 'DiscountedUsage') THEN 0
             ELSE unblended END AS billed_cost,
        CASE li_type
            WHEN 'SavingsPlanCoveredUsage' THEN sp_effective
            WHEN 'DiscountedUsage' THEN ri_effective
            WHEN 'SavingsPlanUpfrontFee' THEN 0
            WHEN 'Fee' THEN CASE WHEN commitment_id IS NOT NULL THEN 0 ELSE unblended END
            ELSE unblended END AS effective_cost,
        CASE WHEN li_type IN ('Usage', 'SavingsPlanCoveredUsage', 'DiscountedUsage')
            THEN list_cost END AS on_demand_equiv_cost,
        tags
    FROM base
    WHERE li_type NOT IN ('SavingsPlanRecurringFee', 'RIFee')

    UNION ALL  -- recurring commitment fee as billed
    SELECT {common}, 'Purchase', 'Commitment-Based', commitment_id, commitment_type, NULL,
        usage_quantity, usage_unit, NULL, NULL, unblended, 0, NULL, tags
    FROM base
    WHERE li_type IN ('SavingsPlanRecurringFee', 'RIFee')

    UNION ALL  -- the unused part of the commitment for that hour
    SELECT {common}, 'Usage', 'Commitment-Based', commitment_id, commitment_type, 'Unused',
        NULL, NULL, NULL, NULL, 0,
        CASE li_type WHEN 'SavingsPlanRecurringFee' THEN sp_unused ELSE ri_unused END, NULL, tags
    FROM base
    WHERE li_type IN ('SavingsPlanRecurringFee', 'RIFee')
        AND CASE li_type WHEN 'SavingsPlanRecurringFee' THEN sp_unused ELSE ri_unused END > 0
    """
    return _run(table, sql)


_AZURE_SIZE = re.compile(
    r"^(?:Standard_|Basic_)?([A-Za-z]+)\d+(?:-\d+)?([A-Za-z]*)(_v\d+)?", re.IGNORECASE
)


def instance_family(instance_type: str | None) -> str | None:
    """m5.large -> m5; db.r6g.xlarge -> r6g; Standard_D4s_v5 -> Ds_v5."""
    if not instance_type:
        return None
    if "." in instance_type:
        parts = instance_type.split(".")
        return parts[-2] if len(parts) >= 2 else None
    m = _AZURE_SIZE.match(instance_type)
    if m:
        return f"{m.group(1)}{m.group(2) or ''}{m.group(3) or ''}"
    return None


def _with_derived(table: pa.Table) -> pa.Table:
    """Fill instance_family from instance_type where the source didn't provide it."""
    if table.num_rows == 0:
        return table
    types = table.column("instance_type").to_pylist()
    families = table.column("instance_family").to_pylist()
    filled = [f or instance_family(t) for f, t in zip(families, types, strict=True)]
    idx = USAGE_SCHEMA.get_field_index("instance_family")
    return table.set_column(idx, USAGE_SCHEMA.field(idx), pa.array(filled, pa.string()))

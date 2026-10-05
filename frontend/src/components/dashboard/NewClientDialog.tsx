"use client";

import { useState } from "react";

import { Button } from "@/components/ui";
import { Field, Modal, Select, TextInput } from "@/components/ui/Field";
import { api } from "@/lib/api";
import { RISK_PROFILES, type RiskProfile } from "@/lib/types";

export function NewClientDialog({ open, onClose, onCreated }: { open: boolean; onClose: () => void; onCreated: (id: string) => void }) {
  const [name, setName] = useState("");
  const [risk, setRisk] = useState<RiskProfile>("balanced");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  return (
    <Modal open={open} title="New client" onClose={onClose}>
      <form
        className="space-y-3"
        onSubmit={async (e) => {
          e.preventDefault();
          setBusy(true);
          setError(null);
          try {
            const t = await api.createTenant(name, risk);
            setName("");
            onCreated(t.id);
          } catch (err) {
            setError((err as Error).message);
          } finally {
            setBusy(false);
          }
        }}
      >
        <Field label="Name">
          <TextInput autoFocus required value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. My AWS account" />
        </Field>
        <Field label="Default risk profile">
          <Select value={risk} onChange={(e) => setRisk(e.target.value as RiskProfile)}>
            {RISK_PROFILES.map((p) => (
              <option key={p} value={p}>
                {p}
              </option>
            ))}
          </Select>
        </Field>
        {error && <p className="text-[12px] text-critical">{error}</p>}
        <div className="flex justify-end gap-2 pt-1">
          <Button type="button" variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" variant="primary" disabled={busy || !name.trim()}>
            Create client
          </Button>
        </div>
      </form>
    </Modal>
  );
}

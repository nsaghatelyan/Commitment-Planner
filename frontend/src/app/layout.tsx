import type { Metadata } from "next";

import "./globals.css";

export const metadata: Metadata = {
  title: "Commitment Planner",
  description: "Savings plan and reservation recommendations for AWS and Azure",
};

// Applies the theme before first paint to avoid a flash: ?theme=light|dark wins (handy for
// demo links), then the saved choice.
const themeScript = `try{var q=new URLSearchParams(location.search).get("theme");var t=q||localStorage.getItem("theme");if(t==="light"||t==="dark")document.documentElement.setAttribute("data-theme",t)}catch(e){}`;

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeScript }} />
      </head>
      <body>{children}</body>
    </html>
  );
}

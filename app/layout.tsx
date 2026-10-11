import type { Metadata } from "next";

import "./globals.css";

export const metadata: Metadata = {
  title: "Dormammu",
  description: "Phone calls, handled in parallel by an AI that says so first.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className="h-full antialiased">
      <body className="min-h-full flex flex-col bg-bg text-fg font-sans">{children}</body>
    </html>
  );
}

import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Voice Healthcare Agent",
  description: "Web client for the voice appointment booking agent (Phase 1 setup).",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" className="h-full antialiased">
      <body className="min-h-full flex flex-col font-sans">{children}</body>
    </html>
  );
}

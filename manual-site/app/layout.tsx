import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: {
    default: "Shuttlers Match App 操作マニュアル",
    template: "%s | Shuttlers Match App 操作マニュアル",
  },
  description:
    "Shuttlers Match Appの参加者・管理者向け操作マニュアルです。",
  other: {
    "codex-preview": "development",
  },
  icons: {
    icon: "/favicon.svg",
    shortcut: "/favicon.svg",
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="ja">
      <body>{children}</body>
    </html>
  );
}

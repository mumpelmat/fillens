import type { Metadata } from "next";
import "./globals.css";
import "./training.css";
import "./dashboard.css";
import "./learning.css";

export const metadata: Metadata = {
  title: "Fillens · Füllstandsportal",
  description:
    "Behälter im Blick. Füllstände erfassen und Entwicklungen verstehen.",
};
export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="de">
      <body>{children}</body>
    </html>
  );
}

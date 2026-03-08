import type { ReactNode } from "react";

export const metadata = {
  title: "Visual Review Fixture",
  description: "Sample Next.js app wired for AI Code Agent screenshot review.",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body
        style={{
          margin: 0,
          fontFamily: 'Georgia, "Times New Roman", serif',
          background: "linear-gradient(180deg, #f7f2e8 0%, #efe3d0 100%)",
          color: "#201815",
        }}
      >
        {children}
      </body>
    </html>
  );
}
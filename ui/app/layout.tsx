import type { Metadata } from "next";
import "./globals.css";

import TabNav from "@/components/TabNav";

export const metadata: Metadata = {
  title: "Job Pipeline",
  description: "Local, Claude-powered job-search dashboard",
};

// Runs BEFORE first paint to set the theme class — no flash of the wrong
// theme. Reads the saved choice, falling back to the OS preference. Then
// flags `theme-ready` so CSS transitions only kick in after initial paint.
const themeInit = `
(function () {
  try {
    var t = localStorage.getItem('theme');
    var dark = t ? t === 'dark' : window.matchMedia('(prefers-color-scheme: dark)').matches;
    if (dark) document.documentElement.classList.add('dark');
  } catch (e) {}
  requestAnimationFrame(function () {
    document.documentElement.classList.add('theme-ready');
  });
})();
`;

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" className="h-full antialiased" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeInit }} />
      </head>
      <body className="min-h-full flex flex-col bg-slate-50 text-slate-800 dark:bg-slate-950 dark:text-slate-100">
        <TabNav />
        {children}
      </body>
    </html>
  );
}

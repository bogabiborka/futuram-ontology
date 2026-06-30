import "./globals.css";

export const metadata = {
  title: "Bench Observer — fq vs composition",
  description: "Read-only live view of the SPARQL LLM benchmark.",
};

export default function RootLayout({ children }) {
  return (
    <html lang="en">
      <body className="antialiased">{children}</body>
    </html>
  );
}

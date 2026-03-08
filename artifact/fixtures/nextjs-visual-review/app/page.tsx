import { SignalBoard } from "../components/signal-board";

const highlights = [
  { label: "Open issues", value: "18", note: "2 need product decisions" },
  { label: "Ship confidence", value: "86%", note: "visual regressions under review" },
  { label: "Release train", value: "Tue", note: "artifact review closes Monday" },
];

export default function HomePage() {
  return (
    <main
      style={{
        minHeight: "100vh",
        display: "grid",
        placeItems: "center",
        padding: "48px 20px",
      }}
    >
      <SignalBoard state="ready" items={highlights} />
    </main>
  );
}
type SignalBoardState = "loading" | "empty" | "error" | "ready";

type SignalItem = {
  label: string;
  value: string;
  note: string;
};

type SignalBoardProps = {
  state: SignalBoardState;
  items: SignalItem[];
};

export function SignalBoard({ state, items }: SignalBoardProps) {
  if (state === "loading") {
    return <section>Loading signals...</section>;
  }

  if (state === "error") {
    return <section>Signal feed unavailable.</section>;
  }

  if (state === "empty" || items.length === 0) {
    return <section>No signals yet.</section>;
  }

  return (
    <section
      style={{
        width: "min(980px, 100%)",
        borderRadius: 28,
        padding: 28,
        background: "rgba(255,255,255,0.72)",
        boxShadow: "0 28px 80px rgba(60, 35, 10, 0.14)",
        backdropFilter: "blur(16px)",
      }}
    >
      <p style={{ letterSpacing: "0.18em", textTransform: "uppercase", color: "#7b6657" }}>
        Visual Review Fixture
      </p>
      <h1 style={{ fontSize: "clamp(3rem, 7vw, 5.8rem)", margin: "12px 0 8px" }}>
        Editorial Signal Board
      </h1>
      <p style={{ maxWidth: 640, fontSize: "1.05rem", lineHeight: 1.7, color: "#594536" }}>
        A small Next.js route used to demonstrate screenshot capture and manifest generation for the reviewer.
      </p>
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))",
          gap: 16,
          marginTop: 24,
        }}
      >
        {items.map((item) => (
          <article
            key={item.label}
            style={{
              padding: 18,
              borderRadius: 20,
              background: "#fff9f2",
              border: "1px solid rgba(74, 50, 23, 0.08)",
            }}
          >
            <p style={{ margin: 0, color: "#7f6c5b", fontSize: "0.88rem" }}>{item.label}</p>
            <strong style={{ display: "block", marginTop: 10, fontSize: "2rem" }}>{item.value}</strong>
            <p style={{ marginBottom: 0, color: "#4c3829" }}>{item.note}</p>
          </article>
        ))}
      </div>
    </section>
  );
}
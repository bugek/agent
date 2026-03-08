"use client";

type ErrorProps = {
  error: Error & { digest?: string };
  reset: () => void;
};

export default function ErrorPage({ reset }: ErrorProps) {
  return (
    <main
      style={{
        minHeight: "100vh",
        display: "grid",
        placeItems: "center",
        padding: 24,
      }}
    >
      <div style={{ maxWidth: 520, textAlign: "center" }}>
        <h1>Preview interrupted</h1>
        <p>The visual review route failed before screenshots could be captured.</p>
        <button type="button" onClick={reset}>
          Try again
        </button>
      </div>
    </main>
  );
}
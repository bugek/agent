import { MetricsCard } from "../../components/metrics-card";
import { loadDashboardMetrics } from "../../lib/analytics";

export default async function DashboardPage() {
  const metrics = await loadDashboardMetrics();

  return (
    <main>
      {metrics.map((metric) => (
        <MetricsCard key={metric.id} label={metric.label} value={metric.value} />
      ))}
    </main>
  );
}

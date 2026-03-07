type MetricsCardProps = {
  label: string;
  value: string;
};

export function MetricsCard({ label, value }: MetricsCardProps) {
  return <article><h2>{label}</h2><p>{value}</p></article>;
}

import ReactECharts from "echarts-for-react";
import type { SeriesPoint } from "../lib/api";

type Props = {
  title: string;
  data: SeriesPoint[];
  kind?: "bar" | "pie";
  orientation?: "vertical" | "horizontal";
  onPointClick?: (name: string) => void;
};

export function ChartPanel({ title, data, kind = "bar", orientation = "vertical", onPointClick }: Props) {
  const option =
    kind === "pie"
      ? {
          tooltip: { trigger: "item" },
          series: [{ type: "pie", radius: ["42%", "72%"], data }]
        }
      : orientation === "horizontal"
        ? {
            tooltip: { trigger: "axis" },
            grid: { top: 16, right: 24, bottom: 24, left: 112 },
            xAxis: { type: "value" },
            yAxis: { type: "category", data: data.map((item) => item.name), axisLabel: { interval: 0 } },
            series: [{ type: "bar", data: data.map((item) => item.value), itemStyle: { color: "#0b73bf" } }]
          }
      : {
          tooltip: { trigger: "axis" },
          grid: { top: 16, right: 16, bottom: 56, left: 48 },
          xAxis: { type: "category", data: data.map((item) => item.name), axisLabel: { rotate: 35, interval: 0 } },
          yAxis: { type: "value" },
          series: [{ type: "bar", data: data.map((item) => item.value), itemStyle: { color: "#0b73bf" } }]
        };

  return (
    <section className="min-h-[320px] rounded-[24px] border border-petroblue/10 bg-white/95 p-5 shadow-card">
      <div className="mb-3 text-sm font-semibold uppercase tracking-wide text-petroink">{title}</div>
      <ReactECharts
        option={option}
        style={{ height: 260 }}
        onEvents={onPointClick ? { click: (params: { name?: string }) => params.name && onPointClick(params.name) } : undefined}
      />
    </section>
  );
}

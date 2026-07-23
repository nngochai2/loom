import { NavLink } from "react-router-dom";
import { cn } from "@/lib/utils";

/** Persistent top nav shell (ADR-0013), amended to 4 tabs by ADR-0026. */
const TABS = [
  { to: "/ingest", label: "Ingest" },
  { to: "/rules", label: "Rules" },
  { to: "/graph", label: "Graph" },
  { to: "/instances", label: "Instances" },
];

export function TopNav() {
  return (
    <header className="border-b border-neutral-200 bg-white">
      <div className="flex h-14 items-center gap-8 px-6">
        <span className="text-sm font-semibold tracking-tight">Loom</span>
        <nav className="flex h-full gap-1">
          {TABS.map((tab) => (
            <NavLink
              key={tab.to}
              to={tab.to}
              className={({ isActive }) =>
                cn(
                  "flex items-center border-b-2 px-3 text-sm font-medium transition-colors",
                  isActive
                    ? "border-neutral-900 text-neutral-900"
                    : "border-transparent text-neutral-500 hover:text-neutral-900",
                )
              }
            >
              {tab.label}
            </NavLink>
          ))}
        </nav>
      </div>
    </header>
  );
}

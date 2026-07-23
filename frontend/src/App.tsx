import { Navigate, Route, BrowserRouter, Routes } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { TopNav } from "@/components/nav";
import { Toaster } from "@/components/ui/toaster";
import { ConfirmDialogProvider } from "@/components/confirm-dialog";
import { Ingest } from "@/pages/Ingest";
import { Rules } from "@/pages/Rules";
import { Graph } from "@/pages/Graph";
import { Instances } from "@/pages/Instances";

// One shared QueryClientProvider at the app root (ADR-0015) — every page's
// server state, including job-status polling, goes through this client.
const queryClient = new QueryClient();

function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <ConfirmDialogProvider>
        <BrowserRouter>
          {/* Desktop-only persistent top nav (ADR-0013), 4 tabs (ADR-0026) */}
          <TopNav />
          <Routes>
            <Route path="/" element={<Navigate to="/ingest" replace />} />
            <Route path="/ingest" element={<Ingest />} />
            <Route path="/rules" element={<Rules />} />
            <Route path="/graph" element={<Graph />} />
            <Route path="/instances" element={<Instances />} />
          </Routes>
        </BrowserRouter>
        <Toaster />
      </ConfirmDialogProvider>
    </QueryClientProvider>
  );
}

export default App;

import { useEffect, useMemo, useRef, useState } from "react";
import "./App.css";
import axios from "axios";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "./components/ui/tabs";
import { Button } from "./components/ui/button";
import { Badge } from "./components/ui/badge";
import { Separator } from "./components/ui/separator";
import { Cpu, RefreshCcw } from "lucide-react";

const BACKEND_URL = process.env.REACT_APP_BACKEND_URL;
const API = `${BACKEND_URL}/api`;

function usePolling(url, intervalMs = 1000) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const timerRef = useRef(null);

  useEffect(() => {
    let mounted = true;

    const tick = async () => {
      try {
        const res = await axios.get(url, { timeout: 8000 });
        if (mounted) setData(res.data);
      } catch (e) {
        if (mounted) setError(e);
      }
    };

    tick();
    timerRef.current = setInterval(tick, intervalMs);

    return () => {
      mounted = false;
      if (timerRef.current) clearInterval(timerRef.current);
    };
  }, [url, intervalMs]);

  return { data, error };
}

const Header = ({ connected, sinceConnectedMs }) => {
  useEffect(() => {
    // enforce dark mode tokens from shadcn
    document.documentElement.classList.add("dark");
  }, []);
  return (
    <div className="header sticky top-0 z-10 w-full backdrop-blur-md">
      <div className="max-w-6xl mx-auto px-6 py-3 flex items-center gap-3">
        <Cpu className="w-5 h-5" />
        <div className="text-sm brand">Rugs.fun Data Service • Live HUD</div>
        <div className="ml-auto flex items-center gap-3">
          <div className="flex items-center gap-2 text-xs">
            <span className={`badge-dot ${connected ? "bg-emerald-500" : "bg-red-500"}`} />
            <span className="text-muted-foreground">{connected ? "Connected" : "Disconnected"}</span>
          </div>
          {connected && (
            <Badge variant="secondary" className="text-[10px] bg-[var(--rugs-primary)] text-[var(--rugs-primary-fore)] border border-[var(--rugs-primary-hover)]">{Math.floor((sinceConnectedMs || 0) / 1000)}s session</Badge>
          )}
        </div>
      </div>
    </div>
  );
};

const LiveOverview = ({ live, onRefresh }) => {
  const items = [
    { label: "Game ID", value: live?.gameId || "-" },
    { label: "Phase", value: live?.phase || "-" },
    { label: "Tick", value: live?.tickCount ?? "-" },
    { label: "Price", value: live?.price ? live.price.toFixed(6) : "-" },
    { label: "Cooldown", value: live?.cooldownTimer ?? "-" },
    { label: "Seed Hash", value: live?.provablyFair?.serverSeedHash ? `${live.provablyFair.serverSeedHash.slice(0, 10)}…` : "-" },
  ];

  return (
    <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3">
      {items.map((it) => (
        <div key={it.label} className="hud-card px-3 py-3">
          <div className="kv">{it.label}</div>
          <div className="kv-val mt-1 truncate" title={String(it.value)}>{it.value}</div>
        </div>
      ))}
      <Button onClick={onRefresh} variant="secondary" className="justify-self-start h-10 btn-primary"><RefreshCcw className="w-4 h-4 mr-2" />Refresh</Button>
    </div>
  );
};

const JsonPane = ({ data }) => (
  <div className="hud-card p-3 overflow-auto max-h-[420px]">
    <pre className="code-block text-xs text-muted-foreground">{data ? JSON.stringify(data, null, 2) : "No data yet"}</pre>
  </div>
);

function App() {
  const { data: conn } = usePolling(`${API}/connection`, 1500);
  const { data: live, error: liveErr } = usePolling(`${API}/live`, 1000);
  const { data: snaps } = usePolling(`${API}/snapshots?limit=25`, 3000);
  const { data: games } = usePolling(`${API}/games?limit=10`, 5000);
  const { data: currentGame } = usePolling(`${API}/games/current`, 2000);
  const { data: prng } = usePolling(`${API}/prng/tracking?limit=10`, 5000);

  const refresh = async () => {
    try {
      await axios.get(`${API}/health`);
    } catch (e) {}
  };

  return (
    <div className="min-h-screen">
      <Header connected={!!conn?.connected} sinceConnectedMs={conn?.since_connected_ms} />
      <main className="max-w-6xl mx-auto px-6 py-6 space-y-6">
        <LiveOverview live={live} onRefresh={refresh} />
        <div className="tabs-bg">
          <Tabs defaultValue="live" className="w-full p-3">
            <TabsList className="grid grid-cols-4 w-full">
              <TabsTrigger value="live" className="text-xs">Live State</TabsTrigger>
              <TabsTrigger value="snapshots" className="text-xs">Recent Snapshots</TabsTrigger>
              <TabsTrigger value="games" className="text-xs">Games</TabsTrigger>
              <TabsTrigger value="prng" className="text-xs">PRNG Tracking</TabsTrigger>
            </TabsList>
            <TabsContent value="live" className="mt-3">
              <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
                <div>
                  <div className="text-xs mb-2 text-muted-foreground">Live gameStateUpdate payload</div>
                  <JsonPane data={live} />
                </div>
                <div>
                  <div className="text-xs mb-2 text-muted-foreground">Connection</div>
                  <JsonPane data={conn} />
                </div>
              </div>
            </TabsContent>
            <TabsContent value="snapshots" className="mt-3">
              <JsonPane data={snaps} />
            </TabsContent>
            <TabsContent value="games" className="mt-3">
              <JsonPane data={{ current: currentGame, recent: games }} />
            </TabsContent>
            <TabsContent value="prng" className="mt-3">
              <JsonPane data={prng} />
            </TabsContent>
          </Tabs>
        </div>
        {liveErr && (
          <div className="text-xs text-red-400">Error loading live data. Ensure backend is connected.</div>
        )}
        <Separator className="my-2" />
        <div className="text-[10px] text-muted-foreground">HUD optimized for developers • Tabs over stacks • No simulated data</div>
      </main>
    </div>
  );
}

export default App;
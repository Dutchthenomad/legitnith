import { useEffect, useMemo, useRef, useState } from "react";
import "./App.css";
import axios from "axios";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "./components/ui/tabs";
import { Button } from "./components/ui/button";
import { Badge } from "./components/ui/badge";
import { Separator } from "./components/ui/separator";
import { FixedSizeList as List } from "react-window";
import { Cpu, RefreshCcw, Plus, Trash2 } from "lucide-react";

const BACKEND_URL = process.env.REACT_APP_BACKEND_URL;
const API = `${BACKEND_URL}/api`;

// Feature flag (toggleable)
const HUD_FEATURES_ENABLED = true;

// Ring buffer implementation (drop-tail with suppressed count)
class RingBuffer {
  constructor(capacity = 10000) {
    this.capacity = capacity;
    this.buffer = new Array(capacity);
    this.start = 0;
    this.size = 0;
    this.suppressed = 0;
  }
  push(item) {
    if (this.size < this.capacity) {
      const idx = (this.start + this.size) % this.capacity;
      this.buffer[idx] = item;
      this.size += 1;
    } else {
      // drop-tail behavior
      this.suppressed += 1;
      const idx = (this.start + this.size - 1) % this.capacity;
      this.buffer[idx] = item; // overwrite last index
    }
  }
  toArray() {
    const out = [];
    for (let i = 0; i < this.size; i++) {
      out.push(this.buffer[(this.start + i) % this.capacity]);
    }
    return out;
  }
  clear() {
    this.start = 0;
    this.size = 0;
    this.suppressed = 0;
  }
}

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
            <Badge
              variant="secondary"
              className="text-[10px] bg-[var(--rugs-primary)] text-[var(--rugs-primary-fore)] border border-[var(--rugs-primary-hover)]"
            >
              {Math.floor((sinceConnectedMs || 0) / 1000)}s session
            </Badge>
          )}
        </div>
      </div>
    </div>
  );
};

const LiveOverview = ({ live, prngStatus, onRefresh, onVerify, verifying }) => {
  const items = [
    { label: "Game ID", value: live?.gameId || "-" },
    { label: "Phase", value: live?.phase || "-" },
    { label: "Tick", value: live?.tickCount ?? "-" },
    { label: "Price", value: live?.price ? live.price.toFixed(6) : "-" },
    { label: "Cooldown", value: live?.cooldownTimer ?? "-" },
    {
      label: "Seed Hash",
      value: live?.provablyFair?.serverSeedHash
        ? `${live.provablyFair.serverSeedHash.slice(0, 10)}…`
        : "-",
    },
  ];

  return (
    <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-7 gap-3">
      {items.map((it) => (
        <div key={it.label} className="hud-card px-3 py-3">
          <div className="kv">{it.label}</div>
          <div className="kv-val mt-1 truncate" title={String(it.value)}>
            {it.value}
          </div>
        </div>
      ))}
      <div className="hud-card px-3 py-3 flex items-center justify-between gap-2">
        <div>
          <div className="kv">PRNG</div>
          <div className="kv-val mt-1" title={prngStatus?.status || "-"}>
            {prngStatus?.status || "-"}
          </div>
        </div>
        <div className="flex gap-2">
          <Button onClick={onRefresh} variant="secondary" className="h-8 btn-primary">
            <RefreshCcw className="w-3 h-3 mr-1" />Ping
          </Button>
          <Button onClick={onVerify} disabled={!live?.gameId || verifying} className="h-8 btn-primary">
            {verifying ? "Verifying..." : "Verify"}
          </Button>
        </div>
      </div>
    </div>
  );
};

const JsonPane = ({ data }) => (
  <div className="hud-card p-3 overflow-auto max-h-[420px]">
    <pre className="code-block text-xs text-muted-foreground">
      {data ? JSON.stringify(data, null, 2) : "No data yet"}
    </pre>
  </div>
);

const HealthStrip = ({ wsConnected, metrics, bufferDepth, suppressed, lastEventIso }) => (
  <div className="hud-card px-3 py-2 flex items-center gap-4">
    <div className="flex items-center gap-2 text-xs">
      <span className={`badge-dot ${wsConnected ? "bg-emerald-500" : "bg-red-500"}`} />
      <span>WS: {wsConnected ? "OK" : "Down"}</span>
    </div>
    <div className="text-xs">
      Msgs/sec: {typeof metrics?.messagesPerSecond1m === "number" ? metrics.messagesPerSecond1m.toFixed(2) : "0.00"}
    </div>
    <div className="text-xs">Buffer: {bufferDepth}{suppressed > 0 ? ` (drop ${suppressed})` : ""}</div>
    <div className="text-xs">Last evt: {lastEventIso ? new Date(lastEventIso).toLocaleTimeString() : "-"}</div>
  </div>
);

// Simple schema-driven filter builder
const operatorsByType = {
  string: [
    { key: "eq", label: "=" },
    { key: "contains", label: "contains" },
    { key: "starts", label: "startsWith" },
  ],
  number: [
    { key: "eq", label: "=" },
    { key: "neq", label: "!=" },
    { key: "gt", label: ">" },
    { key: "gte", label: ">=" },
    { key: "lt", label: "<" },
    { key: "lte", label: "<=" },
  ],
  boolean: [
    { key: "is", label: "is" },
  ],
};

const FilterToolbar = ({
  filters,
  setFilters,
  regexStr,
  setRegexStr,
  regexValid,
  onPresetSave,
  onPresetApply,
  schemaItems,
  rules,
  setRules,
}) => {
  const addRule = () => {
    setRules((r) => {
      if (r.length >= 5) return r;
      const defaultEvent = (schemaItems && schemaItems.length > 0)
        ? (schemaItems.find((it) => it.key === "gameStateUpdate")?.key || schemaItems[0].key)
        : "gameStateUpdate";
      const fields = (() => {
        const it = (schemaItems || []).find((i) => i.key === defaultEvent);
        return it && it.properties ? Object.keys(it.properties) : [];
      })();
      const defaultField = fields.includes("gameId") ? "gameId" : (fields[0] || "");
      return [...r, { event: defaultEvent, field: defaultField, op: "eq", val: "" }];
    });
  };
  const removeRule = (idx) => {
    setRules((r) => r.filter((_, i) => i !== idx));
  };
  const onRuleChange = (idx, patch) => {
    setRules((r) => r.map((it, i) => (i === idx ? { ...it, ...patch } : it)));
  };
  const fieldsForEvent = (eventKey) => {
    const it = schemaItems.find((i) => i.key === eventKey);
    if (!it || !it.properties) return [];
    return Object.entries(it.properties).map(([k, v]) => ({ key: k, type: v.type || "string" }));
  };

  return (
    <div className="hud-card px-3 py-2 flex flex-col gap-2">
      <div className="flex flex-wrap items-center gap-2">
        <div className="flex items-center gap-2">
          <Button size="sm" variant={filters.gs ? "default" : "secondary"} onClick={() => setFilters({ ...filters, gs: !filters.gs })}>game_state</Button>
          <Button size="sm" variant={filters.trade ? "default" : "secondary"} onClick={() => setFilters({ ...filters, trade: !filters.trade })}>trade</Button>
          <Button size="sm" variant={filters.god ? "default" : "secondary"} onClick={() => setFilters({ ...filters, god: !filters.god })}>god_candle</Button>
          <Button size="sm" variant={filters.rug ? "default" : "secondary"} onClick={() => setFilters({ ...filters, rug: !filters.rug })}>rug</Button>
          <Button size="sm" variant={filters.side ? "default" : "secondary"} onClick={() => setFilters({ ...filters, side: !filters.side })}>side_bet</Button>
        </div>
        <div className="ml-auto flex items-center gap-2">
          <Button size="sm" onClick={onPresetSave}>Save preset</Button>
          <div className="flex items-center gap-1">
            {[0,1,2,3,4].map((i) => (
              <Button key={i} size="sm" variant="secondary" onClick={() => onPresetApply(i)}>P{i+1}</Button>
            ))}
          </div>
        </div>
      </div>

      <div className="text-[11px] text-muted-foreground">Filters (AND across rules, applied per matching event type)</div>
      {rules.map((rule, idx) => {
        const fields = fieldsForEvent(rule.event);
        const fieldType = (fields.find((f) => f.key === rule.field)?.type) || "string";
        const ops = operatorsByType[fieldType] || operatorsByType.string;
        return (
          <div key={idx} className="flex items-center gap-2">
            <select className="px-2 py-1 text-xs rounded bg-transparent border border-[var(--rugs-border)]" value={rule.event} onChange={(e) => onRuleChange(idx, { event: e.target.value, field: "" })}>
              {schemaItems.map((it) => (
                <option key={it.key} value={it.key}>{it.key}</option>
              ))}
            </select>
            <select className="px-2 py-1 text-xs rounded bg-transparent border border-[var(--rugs-border)]" value={rule.field} onChange={(e) => onRuleChange(idx, { field: e.target.value })}>
              <option value="">field…</option>
              {fields.map((f) => (
                <option key={f.key} value={f.key}>{f.key}</option>
              ))}
            </select>
            <select className="px-2 py-1 text-xs rounded bg-transparent border border-[var(--rugs-border)]" value={rule.op} onChange={(e) => onRuleChange(idx, { op: e.target.value })}>
              {ops.map((o) => (
                <option key={o.key} value={o.key}>{o.label}</option>
              ))}
            </select>
            {fieldType === "boolean" ? (
              <select className="px-2 py-1 text-xs rounded bg-transparent border border-[var(--rugs-border)]" value={String(rule.val)} onChange={(e) => onRuleChange(idx, { val: e.target.value === "true" })}>
                <option value="true">true</option>
                <option value="false">false</option>
              </select>
            ) : (
              <input className="px-2 py-1 text-xs rounded bg-transparent border border-[var(--rugs-border)]" value={rule.val ?? ""} onChange={(e) => onRuleChange(idx, { val: e.target.value })} placeholder="value" style={{ minWidth: 120 }} />
            )}
            <Button size="icon" variant="secondary" onClick={() => removeRule(idx)}><Trash2 className="w-3 h-3" /></Button>
          </div>
        );
      })}
      <div>
        <Button size="sm" variant="secondary" onClick={addRule}><Plus className="w-3 h-3 mr-1" />Add rule</Button>
      </div>

      <div className="text-[11px] text-muted-foreground">Advanced (regex against JSON)</div>
      <div className="flex items-center gap-2">
        <input
          className={`px-2 py-1 text-xs rounded border ${regexValid ? "border-[var(--rugs-border)]" : "border-[var(--rugs-danger)]"} bg-transparent`}
          value={regexStr}
          onChange={(e) => setRegexStr(e.target.value)}
          placeholder="regex filter (JSON match)"
          style={{ minWidth: 220 }}
        />
      </div>
    </div>
  );
};

const MessageList = ({ items }) => {
  const Row = ({ index, style }) => {
    const m = items[index];
    const txt = JSON.stringify(m);
    return (
      <div style={style} className="px-2 text-[11px] text-muted-foreground whitespace-nowrap overflow-hidden text-ellipsis">
        <span className="mr-2 font-semibold text-[var(--rugs-primary)]">{m.type}</span>
        {txt}
      </div>
    );
  };
  const height = Math.min(320, Math.max(120, items.length * 26));
  return (
    <div className="hud-card">
      <List height={height} width={"100%"} itemCount={items.length} itemSize={26}>
        {Row}
      </List>
    </div>
  );
};

function App() {
  const [wsConnected, setWsConnected] = useState(false);
  const [buffer] = useState(() => new RingBuffer(10000));
  const [filters, setFilters] = useState({ gs: true, trade: true, god: true, rug: true, side: true });
  const [regexStr, setRegexStr] = useState("");
  const [regexValid, setRegexValid] = useState(true);
  const [lastEventIso, setLastEventIso] = useState(null);

  const { data: conn } = usePolling(`${API}/connection`, 1500);
  const { data: live, error: liveErr } = usePolling(`${API}/live`, 1000);
  const { data: snaps } = usePolling(`${API}/snapshots?limit=25`, 3000);
  const { data: games } = usePolling(`${API}/games?limit=200`, 5000);
  const { data: currentGame } = usePolling(`${API}/games/current`, 2000);
  const { data: prng } = usePolling(`${API}/prng/tracking?limit=10`, 5000);
  const { data: metrics } = usePolling(`${API}/metrics`, 2000);
  const { data: schemasResp } = usePolling(`${API}/schemas`, 15000);

  const [verifying, setVerifying] = useState(false);
  const currentPrng = useMemo(() => {
    if (!currentGame?.id || !prng?.items) return null;
    return prng.items.find((i) => i.gameId === currentGame.id) || null;
  }, [prng, currentGame]);

  // schema items
  const schemaItems = useMemo(() => schemasResp?.items || [], [schemasResp]);
  const [rules, setRules] = useState(() => {
    try {
      const presets = JSON.parse(localStorage.getItem("hud_presets") || "[]");
      return presets[0]?.rules || [];
    } catch (_) { return []; }
  });

  // migrate presets
  useEffect(() => {
    try {
      const presets = JSON.parse(localStorage.getItem("hud_presets") || "[]");
      if (presets.length) {
        // normalize shape
        const p0 = presets[0];
        if (p0 && !("rules" in p0)) {
          const migrated = presets.map((p) => ({ filters: p.f, regex: p.r, rules: [] }));
          localStorage.setItem("hud_presets", JSON.stringify(migrated));
        }
      }
    } catch (_) {}
  }, []);

  const refresh = async () => {
    try {
      await axios.get(`${API}/health`);
    } catch (e) {}
  };

  const verifyNow = async () => {
    if (!currentGame?.id) return;
    setVerifying(true);
    try {
      await axios.post(`${API}/prng/verify/${currentGame.id}`);
    } catch (e) {
      // ignore for HUD
    } finally {
      setTimeout(() => setVerifying(false), 500);
    }
  };

  const onPresetSave = () => {
    try {
      const presets = JSON.parse(localStorage.getItem("hud_presets") || "[]");
      const entry = { filters, regex: regexStr, rules };
      const next = [entry, ...presets].slice(0, 5);
      localStorage.setItem("hud_presets", JSON.stringify(next));
      // force a tick so buttons visually reflect available presets
      setRules((r) => [...r]);
    } catch (_) {}
  };

  const onPresetApply = (idx) => {
    try {
      const presets = JSON.parse(localStorage.getItem("hud_presets") || "[]");
      const p = presets[idx];
      if (p) {
        setFilters({ ...filters, ...(p.filters || {}) });
        setRegexStr(p.regex || "");
        setRules(Array.isArray(p.rules) ? p.rules : []);
      }
    } catch (_) {}
  };

  // WS stream subscription for HUD filter panel
  useEffect(() => {
    if (!HUD_FEATURES_ENABLED) return;
    const url = `${BACKEND_URL}/api/ws/stream`;
    let ws;
    try {
      ws = new WebSocket(url.replace(/^http/, "ws"));
    } catch (e) {
      // ignore
    }
    if (!ws) return;

    ws.onopen = () => setWsConnected(true);
    ws.onclose = () => setWsConnected(false);

    ws.onmessage = (ev) => {
      let msg;
      try {
        msg = JSON.parse(ev.data);
      } catch (_) {
        return;
      }
      if (!msg || typeof msg !== "object") return;
      if (msg.type === "heartbeat" || msg.type === "hello") return;
      buffer.push(msg);
      if (msg.ts) setLastEventIso(msg.ts);
    };

    return () => {
      try {
        ws.close();
      } catch (_) {}
    };
  }, [buffer]);

  // map outbound type to inbound schema key
  const outboundToSchemaKeys = useMemo(() => {
    const map = {};
    (schemaItems || []).forEach((it) => {
      if (it.outboundType) {
        if (!map[it.outboundType]) map[it.outboundType] = new Set();
        map[it.outboundType].add(it.key);
      }
    });
    return map;
  }, [schemaItems]);

  const applyRulesToMessage = (m) => {
    if (!rules || rules.length === 0) return true;
    // map outbound type to one or more schema keys
    const schemaKeysSet = outboundToSchemaKeys[m.type];
    const relevant = rules.filter((r) => {
      if (!schemaKeysSet) return false;
      return schemaKeysSet.has(r.event);
    });
    if (relevant.length === 0) return true;
    try {
      return relevant.every((r) => {
        // Support dot-paths if needed (e.g., payload.nested)
        const getVal = (obj, path) => {
          try {
            if (!path) return undefined;
            if (path.includes('.')) {
              return path.split('.').reduce((o, k) => (o ? o[k] : undefined), obj);
            }
            return obj[path];
          } catch (_) {
            return undefined;
          }
        };
        const val = getVal(m, r.field);
        switch (r.op) {
          case "eq":
            return String(val) === String(r.val);
          case "neq":
            return String(val) !== String(r.val);
          case "gt":
            return Number(val) > Number(r.val);
          case "gte":
            return Number(val) >= Number(r.val);
          case "lt":
            return Number(val) < Number(r.val);
          case "lte":
            return Number(val) <= Number(r.val);
          case "contains":
            return (String(val || "").toLowerCase()).includes(String(r.val || "").toLowerCase());
          case "starts":
            return String(val || "").toLowerCase().startsWith(String(r.val || "").toLowerCase());
          case "is":
            return Boolean(val) === Boolean(r.val);
          default:
            return true;
        }
      });
    } catch (_) {
      return true;
    }
  };

  const filtered = useMemo(() => {
    if (!HUD_FEATURES_ENABLED) return [];
    let arr = buffer.toArray();
    // type filters
    arr = arr.filter((m) => {
      if (m.type === "game_state_update") return filters.gs;
      if (m.type === "trade") return filters.trade;
      if (m.type === "god_candle") return filters.god;
      if (m.type === "rug") return filters.rug;
      if (m.type === "side_bet") return filters.side;
      return false;
    });
    // schema-driven rules
    arr = arr.filter((m) => applyRulesToMessage(m));
    // regex
    if (regexStr) {
      try {
        const r = new RegExp(regexStr, "i");
        arr = arr.filter((m) => r.test(JSON.stringify(m)));
        if (!regexValid) setRegexValid(true);
      } catch (e) {
        if (regexValid) setRegexValid(false);
      }
    } else {
      if (!regexValid) setRegexValid(true);
    }
    return arr.slice(-5000); // safety cap for rendering
  }, [buffer, filters, regexStr, regexValid, rules, schemaItems]);

  // Minimal charts (SVG only)
  const DurationHistogramSVG = ({ items }) => { // container is a hud-card; ensure SVG is responsive within

    const ticks = (items || []).map((g) => Number(g.totalTicks || 0)).filter((n) => Number.isFinite(n) && n >= 0);
    const N = Math.min(200, ticks.length);
    const arr = ticks.slice(0, N);
    if (arr.length === 0) return <div className="hud-card p-3 text-xs text-muted-foreground">No data</div>;
    const binSize = 50;
    const maxTick = Math.max(...arr);
    const bins = Math.max(5, Math.ceil((maxTick + 1) / binSize));
    const counts = new Array(bins).fill(0);
    arr.forEach((t) => { counts[Math.min(bins - 1, Math.floor(t / binSize))] += 1; });
    const w = 400, h = 120, pad = 6;
    const maxCount = Math.max(...counts) || 1;
    const barW = (w - pad * 2) / bins;
    return (
      <div className="hud-card p-3">
        <div className="text-xs mb-2 text-muted-foreground">Duration Histogram (ticks)</div>
        <div style={{ width: "100%", overflow: "hidden" }}>
          <svg width={w} height={h} style={{ maxWidth: "100%", display: "block" }}>
            {counts.map((c, i) => {
              const bh = (c / maxCount) * (h - 20);
              return (
                <rect key={i} x={pad + i * barW + 1} y={h - bh - 10} width={barW - 2} height={bh} fill="#ffc700" />
              );
            })}
          </svg>
        </div>
      </div>
    );
  };

  const PeakSparklineSVG = ({ items }) => { // ensure it respects container width
    const peaks = (items || []).map((g) => Number(g.peakMultiplier || 0)).filter((n) => Number.isFinite(n) && n > 0);
    const N = Math.min(200, peaks.length);
    const arr = peaks.slice(0, N).reverse(); // newest on right
    if (arr.length === 0) return <div className="hud-card p-3 text-xs text-muted-foreground">No data</div>;
    const w = 400, h = 120, pad = 6;
    const maxV = Math.max(...arr);
    const minV = Math.min(...arr);
    const pts = arr.map((v, i) => {
      const x = pad + (i * (w - pad * 2)) / Math.max(1, arr.length - 1);
      const y = h - 10 - ((v - minV) / Math.max(1e-6, (maxV - minV))) * (h - 20);
      return `${x},${y}`;
    });
    return (
      <div className="hud-card p-3">
        <div className="text-xs mb-2 text-muted-foreground">Peak Multiplier Sparkline</div>
        <svg width={w} height={h} style={{ maxWidth: "100%", display: "block" }}>
          <polyline points={pts.join(" ")} fill="none" stroke="#ef7104" strokeWidth="2" />
        </svg>
      </div>
    );
  };

  const bufferDepth = buffer.size;
  const suppressed = buffer.suppressed;

  return (
    <div className="min-h-screen">
      <Header connected={!!conn?.connected} sinceConnectedMs={conn?.since_connected_ms} />
      <main className="max-w-6xl mx-auto px-6 py-6 space-y-6">
        <LiveOverview live={live} prngStatus={currentPrng} onRefresh={refresh} onVerify={verifyNow} verifying={verifying} />

        {HUD_FEATURES_ENABLED && (
          <div className="space-y-3">
            <HealthStrip wsConnected={wsConnected} metrics={metrics} bufferDepth={bufferDepth} suppressed={suppressed} lastEventIso={lastEventIso} />
            <FilterToolbar
              filters={filters}
              setFilters={setFilters}
              regexStr={regexStr}
              setRegexStr={setRegexStr}
              regexValid={regexValid}
              onPresetSave={onPresetSave}
              onPresetApply={onPresetApply}
              schemaItems={schemaItems}
              rules={rules}
              setRules={setRules}
            />
            <MessageList items={filtered} />
          </div>
        )}

        <div className="tabs-bg">
          <Tabs defaultValue="live" className="w-full p-3">
            <TabsList className="grid grid-cols-5 w-full">
              <TabsTrigger value="live" className="text-xs">Live State</TabsTrigger>
              <TabsTrigger value="snapshots" className="text-xs">Recent Snapshots</TabsTrigger>
              <TabsTrigger value="games" className="text-xs">Games</TabsTrigger>
              <TabsTrigger value="prng" className="text-xs">PRNG Tracking</TabsTrigger>
              <TabsTrigger value="diagnostics" className="text-xs">Diagnostics</TabsTrigger>
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
            <TabsContent value="diagnostics" className="mt-3">
              <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                <DurationHistogramSVG items={games?.items || []} />
                <PeakSparklineSVG items={games?.items || []} />
              </div>
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
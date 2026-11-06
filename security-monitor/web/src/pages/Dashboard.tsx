import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { ConfigProvider, Spin, Tooltip as AntTooltip, theme } from 'antd';
import {
  AlertOutlined, ApiOutlined, BulbOutlined, CloudServerOutlined, CompressOutlined, ExpandOutlined,
  GlobalOutlined, ReloadOutlined, ShopOutlined, ThunderboltOutlined, WarningOutlined,
} from '@ant-design/icons';
import { useNavigate, useSearchParams } from 'react-router-dom';
import {
  Area, AreaChart, Bar, BarChart, CartesianGrid, Cell, Pie, PieChart, PolarAngleAxis, RadialBar, RadialBarChart,
  ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts';
import { dashboardApi } from '../services/api';
import { useMe } from '../context/MeContext';
import TopologyMap, { HEALTH_COLOR } from '../components/dashboard/TopologyMap';
import SiteDetailDrawer from '../components/dashboard/SiteDetailDrawer';
import type { Alert, AlertTrend, DashboardOverview, DashboardSite, Distribution, Insights } from '../types';
import './Dashboard.css';

const REFRESH_MS = 30000;
const LEVEL_META: Record<string, { label: string; color: string }> = {
  critical: { label: '严重', color: '#ff4d6a' },
  high: { label: '高危', color: '#ff8a3d' },
  medium: { label: '中危', color: '#ffd447' },
  low: { label: '低危', color: '#22e3a5' },
};
const EVENT_LABELS: Record<string, string> = {
  probe_offline: '采集器离线', offline: '设备离线', snmp_unreachable: 'SNMP无响应', interface_down: '端口断开',
  high_cpu: 'CPU过高', high_memory: '内存过高', high_disk: '磁盘过高', login: '账号登录', logout: '账号退出',
  high_risk: '高危操作',
};
const DEVICE_LABELS: Record<string, string> = { switch: '交换机', router: '路由器', firewall: '防火墙', ap: '无线AP', other: '其他' };
const VENDOR_LABELS: Record<string, string> = { huawei: '华为', h3c: '华三', cisco: '思科', ruijie: '锐捷', linux: 'Linux', generic: '通用', unknown: '未识别' };
const PALETTE = ['#20d4ff', '#22e3a5', '#7c6cff', '#ffb547', '#ff6fa8', '#4d9bff', '#9be15d'];

function useCountUp(target: number, duration = 900) {
  const [value, setValue] = useState(0);
  const from = useRef(0);
  useEffect(() => {
    const start = performance.now();
    const begin = from.current;
    let raf = 0;
    const tick = (now: number) => {
      const t = Math.min(1, (now - start) / duration);
      const eased = 1 - Math.pow(1 - t, 3);
      setValue(Math.round(begin + (target - begin) * eased));
      if (t < 1) raf = requestAnimationFrame(tick);
      else from.current = target;
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [target, duration]);
  return value;
}

function Num({ value }: { value: number }) {
  return <>{useCountUp(value)}</>;
}

function Panel({ title, icon, extra, children, className }: {
  title: string; icon?: ReactNode; extra?: ReactNode; children: ReactNode; className?: string;
}) {
  return (
    <div className={`sp-panel ${className || ''}`}>
      <div className="sp-panel-head">
        <span className="sp-panel-title">{icon}{title}</span>
        {extra}
      </div>
      <div className="sp-panel-body">{children}</div>
      <i className="sp-corner tl" /><i className="sp-corner tr" /><i className="sp-corner bl" /><i className="sp-corner br" />
    </div>
  );
}

function Kpi({ label, value, total, unit, icon, tone = 'cyan', hint, onClick }: {
  label: string; value: number; total?: number; unit?: string; icon: ReactNode;
  tone?: 'cyan' | 'green' | 'orange' | 'red' | 'purple'; hint?: string; onClick?: () => void;
}) {
  const rate = total ? Math.round((value / total) * 100) : undefined;
  return (
    <div className={`sp-kpi tone-${tone}`} onClick={onClick} role="button">
      <div className="sp-kpi-icon">{icon}</div>
      <div className="sp-kpi-main">
        <div className="sp-kpi-label">{label}</div>
        <div className="sp-kpi-value">
          <Num value={value} />
          {total !== undefined && <span className="sp-kpi-total">/ {total}</span>}
          {unit && <span className="sp-kpi-total">{unit}</span>}
        </div>
        <div className="sp-kpi-hint">{rate !== undefined ? `在线率 ${rate}%` : hint}</div>
      </div>
      {rate !== undefined && <div className="sp-kpi-bar"><span style={{ width: `${rate}%` }} /></div>}
    </div>
  );
}

function RateBar({ label, value, total, color }: { label: string; value: number; total: number; color: string }) {
  const rate = total ? Math.round((value / total) * 100) : 0;
  return (
    <div className="sp-rate">
      <div className="sp-rate-row"><span>{label}</span><span><b style={{ color }}>{total ? `${rate}%` : '-'}</b> <em>{value}/{total}</em></span></div>
      <div className="sp-rate-track"><span style={{ width: `${rate}%`, background: `linear-gradient(90deg, ${color}33, ${color})`, boxShadow: `0 0 8px ${color}` }} /></div>
    </div>
  );
}

export default function Dashboard() {
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const { me } = useMe();
  const screenRef = useRef<HTMLDivElement>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [overview, setOverview] = useState<DashboardOverview | null>(null);
  const [trend, setTrend] = useState<AlertTrend[]>([]);
  const [recent, setRecent] = useState<(Alert & { site_name?: string })[]>([]);
  const [sites, setSites] = useState<DashboardSite[]>([]);
  const [dist, setDist] = useState<Distribution | null>(null);
  const [insights, setInsights] = useState<Insights | null>(null);
  const [updatedAt, setUpdatedAt] = useState<Date | null>(null);
  const [now, setNow] = useState(new Date());
  const [fullscreen, setFullscreen] = useState(false);
  const selectedSite = params.get('site') ? Number(params.get('site')) : null;

  const load = useCallback(async (silent = false) => {
    if (!silent) setLoading(true);
    setRefreshing(true);
    try {
      const [o, t, r, s, d, i] = await Promise.allSettled([
        dashboardApi.overview(), dashboardApi.alertTrend(7), dashboardApi.recentAlerts(20),
        dashboardApi.sites(), dashboardApi.distribution(), dashboardApi.insights(),
      ]);
      if (o.status === 'fulfilled') setOverview(o.value.data);
      if (t.status === 'fulfilled') setTrend(t.value.data || []);
      if (r.status === 'fulfilled') setRecent(r.value.data || []);
      if (s.status === 'fulfilled') setSites(s.value.data || []);
      if (d.status === 'fulfilled') setDist(d.value.data);
      if (i.status === 'fulfilled') setInsights(i.value.data);
      setUpdatedAt(new Date());
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    load();
    const timer = setInterval(() => load(true), REFRESH_MS);
    const clock = setInterval(() => setNow(new Date()), 1000);
    const onFs = () => setFullscreen(!!document.fullscreenElement);
    document.addEventListener('fullscreenchange', onFs);
    return () => { clearInterval(timer); clearInterval(clock); document.removeEventListener('fullscreenchange', onFs); };
  }, [load]);

  const openSite = (id: number) => setParams({ site: String(id) });
  const closeSite = () => setParams({});
  const toggleFullscreen = () => {
    if (document.fullscreenElement) document.exitFullscreen();
    else screenRef.current?.requestFullscreen?.();
  };
  const follow = (link?: string | null) => {
    if (!link) return;
    const m = link.match(/^\/dashboard\?site=(\d+)/);
    if (m) openSite(Number(m[1]));
    else navigate(link);
  };

  const ranking = useMemo(() => [...sites].sort((a, b) => (a.health_score ?? 0) - (b.health_score ?? 0)).slice(0, 8), [sites]);
  const levelCounts = useMemo(() => ({
    good: sites.filter((s) => s.health_level === 'good').length,
    warn: sites.filter((s) => s.health_level === 'warn').length,
    danger: sites.filter((s) => s.health_level === 'danger').length,
  }), [sites]);
  const levelPie = useMemo(() => (dist?.alert_levels || [])
    .map((l) => ({ ...l, label: LEVEL_META[l.name]?.label || l.name, color: LEVEL_META[l.name]?.color || '#888' }))
    .sort((a, b) => Object.keys(LEVEL_META).indexOf(a.name) - Object.keys(LEVEL_META).indexOf(b.name)), [dist]);
  const totalLevel = levelPie.reduce((s, l) => s + Number(l.value), 0);
  const score = insights?.score ?? 100;
  const scoreColor = HEALTH_COLOR[insights?.level || 'good'];
  const o = overview;
  const alertDelta = o ? o.alerts_24h! - (o.alerts_prev_24h || 0) : 0;

  return (
    <ConfigProvider theme={{
      algorithm: theme.darkAlgorithm,
      token: { colorPrimary: '#20d4ff', colorBgElevated: '#0c1c36', colorBgContainer: '#0e2140', borderRadius: 6 },
    }}>
      <div className={`screen ${fullscreen ? 'is-full' : ''}`} ref={screenRef}>
        <header className="sp-head">
          <div className="sp-head-side">
            <span className="sp-clock">{now.toLocaleTimeString('zh-CN', { hour12: false })}</span>
            <span className="sp-date">{now.toLocaleDateString('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit', weekday: 'short' })}</span>
          </div>
          <div className="sp-title">
            <h1>多门店网络安全态势感知平台</h1>
            <div className="sp-subtitle">
              <span className="sp-live" /> 实时监测 · {me?.data_scope === 'sites' ? `授权范围 ${me.scope_site_count ?? 0} 个站点` : '全部站点'}
            </div>
          </div>
          <div className="sp-head-side right">
            <span className="sp-updated">{updatedAt ? `数据更新 ${updatedAt.toLocaleTimeString('zh-CN', { hour12: false })}` : ''}</span>
            <AntTooltip title="刷新"><button className="sp-btn" onClick={() => load(true)}><ReloadOutlined spin={refreshing} /></button></AntTooltip>
            <AntTooltip title={fullscreen ? '退出全屏' : '全屏大屏'}>
              <button className="sp-btn" onClick={toggleFullscreen}>{fullscreen ? <CompressOutlined /> : <ExpandOutlined />}</button>
            </AntTooltip>
          </div>
        </header>

        {loading && !overview ? <div className="sp-loading"><Spin size="large" /></div> : (
          <>
            <section className="sp-kpis">
              <Kpi label="站点采集在线" value={o?.online_probes || 0} total={o?.probe_sites || 0} icon={<ShopOutlined />}
                tone="cyan" onClick={() => navigate('/sites')} />
              <Kpi label="服务器在线" value={o?.online_servers || 0} total={o?.total_servers || 0} icon={<CloudServerOutlined />}
                tone="green" onClick={() => navigate('/servers')} />
              <Kpi label="网络设备在线" value={o?.online_devices || 0} total={o?.total_devices || 0} icon={<GlobalOutlined />}
                tone="purple" onClick={() => navigate('/network-devices')} />
              <Kpi label="端口 UP" value={o?.interfaces_up || 0} total={o?.interfaces_total || 0} icon={<ApiOutlined />}
                tone="cyan" onClick={() => navigate('/network-devices')} />
              <Kpi label="近 24 小时告警" value={o?.alerts_24h || 0} icon={<AlertOutlined />} tone="orange"
                hint={alertDelta === 0 ? '与前一天持平' : `较前一天 ${alertDelta > 0 ? '↑' : '↓'} ${Math.abs(alertDelta)}`}
                onClick={() => navigate('/alerts')} />
              <Kpi label="待处理告警" value={o?.unresolved_alerts || 0} icon={<WarningOutlined />}
                tone={(o?.unresolved_alerts || 0) > 0 ? 'red' : 'green'} hint={`今日严重 ${o?.critical_alerts_today || 0} 条`}
                onClick={() => navigate('/alerts?status=open')} />
            </section>

            <section className="sp-grid">
              <div className="sp-col">
                <Panel title="综合健康指数" icon={<ThunderboltOutlined />}>
                  <div className="sp-gauge">
                    <ResponsiveContainer width="100%" height={170}>
                      <RadialBarChart innerRadius="72%" outerRadius="100%" startAngle={220} endAngle={-40}
                        data={[{ value: score, fill: scoreColor }]}>
                        <PolarAngleAxis type="number" domain={[0, 100]} tick={false} />
                        <RadialBar dataKey="value" cornerRadius={8} background={{ fill: 'rgba(32,212,255,0.08)' }} />
                      </RadialBarChart>
                    </ResponsiveContainer>
                    <div className="sp-gauge-center">
                      <div className="sp-gauge-num" style={{ color: scoreColor }}><Num value={score} /></div>
                      <div className="sp-gauge-label">{insights?.level === 'good' ? '运行良好' : insights?.level === 'warn' ? '需要关注' : '存在风险'}</div>
                    </div>
                  </div>
                  <div className="sp-tri">
                    <div onClick={() => navigate('/sites')}><b style={{ color: HEALTH_COLOR.good }}>{levelCounts.good}</b><span>良好</span></div>
                    <div onClick={() => navigate('/sites')}><b style={{ color: HEALTH_COLOR.warn }}>{levelCounts.warn}</b><span>注意</span></div>
                    <div onClick={() => navigate('/sites')}><b style={{ color: HEALTH_COLOR.danger }}>{levelCounts.danger}</b><span>异常</span></div>
                  </div>
                </Panel>
                <Panel title="资产在线率" icon={<CloudServerOutlined />}>
                  <RateBar label="站点采集器" value={o?.online_probes || 0} total={o?.probe_sites || 0} color="#20d4ff" />
                  <RateBar label="服务器" value={o?.online_servers || 0} total={o?.total_servers || 0} color="#22e3a5" />
                  <RateBar label="网络设备" value={o?.online_devices || 0} total={o?.total_devices || 0} color="#7c6cff" />
                  <RateBar label="交换机端口" value={o?.interfaces_up || 0} total={o?.interfaces_total || 0} color="#ffb547" />
                </Panel>
                <Panel title="告警等级分布（7 天）" icon={<AlertOutlined />} className="grow">
                  {totalLevel === 0 ? <div className="sp-empty">近 7 天无告警</div> : (
                    <div className="sp-donut">
                      <ResponsiveContainer width="55%" height={160}>
                        <PieChart>
                          <Pie data={levelPie} dataKey="value" nameKey="label" innerRadius={42} outerRadius={64} paddingAngle={3} stroke="none">
                            {levelPie.map((l) => <Cell key={l.name} fill={l.color} />)}
                          </Pie>
                          <Tooltip />
                        </PieChart>
                      </ResponsiveContainer>
                      <ul className="sp-legend">
                        {levelPie.map((l) => (
                          <li key={l.name} onClick={() => navigate(`/alerts?level=${l.name}`)}>
                            <i style={{ background: l.color }} />{l.label}<b>{l.value}</b>
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}
                </Panel>
              </div>

              <div className="sp-col center">
                <Panel title="站点连接拓扑" icon={<GlobalOutlined />} className="topo-panel" extra={
                  <div className="sp-topo-legend">
                    <span><i className="dot good" />正常</span><span><i className="dot warn" />注意</span>
                    <span><i className="dot danger" />异常 / 失联</span><span><i className="dot cloud" />云端</span>
                  </div>
                }>
                  <TopologyMap sites={sites} onSelect={(s) => openSite(s.id)} height={fullscreen ? 520 : 430} />
                </Panel>
                <Panel title="告警趋势（7 天）" icon={<ThunderboltOutlined />}>
                  <ResponsiveContainer width="100%" height={170}>
                    <AreaChart data={trend} margin={{ top: 8, right: 8, left: -20, bottom: 0 }}>
                      <defs>
                        {Object.entries(LEVEL_META).map(([k, v]) => (
                          <linearGradient key={k} id={`g-${k}`} x1="0" y1="0" x2="0" y2="1">
                            <stop offset="0%" stopColor={v.color} stopOpacity={0.6} />
                            <stop offset="100%" stopColor={v.color} stopOpacity={0.02} />
                          </linearGradient>
                        ))}
                      </defs>
                      <CartesianGrid strokeDasharray="3 3" stroke="rgba(120,180,255,0.08)" />
                      <XAxis dataKey="date" tickFormatter={(d: string) => d.slice(5)} tick={{ fill: '#7fa6cf', fontSize: 11 }} />
                      <YAxis allowDecimals={false} tick={{ fill: '#7fa6cf', fontSize: 11 }} />
                      <Tooltip contentStyle={{ background: '#0c1c36', border: '1px solid #1d4a7a' }} />
                      {Object.entries(LEVEL_META).map(([k, v]) => (
                        <Area key={k} type="monotone" dataKey={k} name={v.label} stackId="1" stroke={v.color} fill={`url(#g-${k})`} />
                      ))}
                    </AreaChart>
                  </ResponsiveContainer>
                </Panel>
              </div>

              <div className="sp-col">
                <Panel title="AI 智能研判" icon={<BulbOutlined />} className="ai-panel">
                  <div className="sp-ai-summary"><span className="sp-ai-badge">AI</span>{insights?.summary || '正在分析…'}</div>
                  <ul className="sp-insights">
                    {(insights?.items || []).slice(0, 6).map((item, idx) => (
                      <li key={idx} className={`lv-${item.level} ${item.link ? 'link' : ''}`} onClick={() => follow(item.link)}>
                        <div className="sp-insight-title">{item.title}</div>
                        <div className="sp-insight-detail">{item.detail}</div>
                      </li>
                    ))}
                  </ul>
                </Panel>
                <Panel title="实时告警" icon={<AlertOutlined />} extra={<a onClick={() => navigate('/alerts')}>全部 ›</a>}>
                  {recent.length === 0 ? <div className="sp-empty">暂无告警</div> : (
                    <div className="sp-ticker">
                      <ul className={recent.length > 5 ? 'scroll' : ''} style={{ animationDuration: `${recent.length * 3}s` }}>
                        {(recent.length > 5 ? [...recent, ...recent] : recent).map((a, idx) => (
                          <li key={`${a.id}-${idx}`} onClick={() => navigate(`/alerts?keyword=${encodeURIComponent(a.source_name)}`)}>
                            <span className="lv" style={{ background: LEVEL_META[a.level]?.color }}>{LEVEL_META[a.level]?.label || a.level}</span>
                            <span className="src">{a.site_name ? `${a.site_name} · ` : ''}{a.source_name}</span>
                            <span className="desc">{EVENT_LABELS[a.event_type] || a.event_description}</span>
                            <span className="time">{new Date(a.created_at).toLocaleTimeString('zh-CN', { hour12: false, hour: '2-digit', minute: '2-digit' })}</span>
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}
                </Panel>
                <Panel title="站点健康排行（低→高）" icon={<ShopOutlined />} className="grow">
                  {ranking.length === 0 ? <div className="sp-empty">暂无站点</div> : (
                    <ul className="sp-rank">
                      {ranking.map((s, idx) => (
                        <li key={s.id} onClick={() => openSite(s.id)}>
                          <span className={`no n${idx}`}>{idx + 1}</span>
                          <span className="name">{s.name}</span>
                          <span className="bar"><i style={{ width: `${s.health_score ?? 0}%`, background: HEALTH_COLOR[s.health_level || 'good'] }} /></span>
                          <b style={{ color: HEALTH_COLOR[s.health_level || 'good'] }}>{s.health_score ?? '-'}</b>
                        </li>
                      ))}
                    </ul>
                  )}
                </Panel>
              </div>
            </section>

            <section className="sp-bottom">
              <Panel title="资源负载 Top" icon={<ThunderboltOutlined />}>
                {(dist?.top_servers?.length || 0) + (dist?.top_devices?.length || 0) === 0 ? <div className="sp-empty">暂无在线资源数据</div> : (
                  <ul className="sp-top">
                    {[
                      ...(dist?.top_servers || []).map((s) => ({ key: `s${s.id}`, name: s.name, site: s.site_name, kind: '服务器', cpu: s.cpu, mem: s.memory, disk: s.disk, to: '/servers' })),
                      ...(dist?.top_devices || []).map((d) => ({ key: `d${d.id}`, name: d.name, site: d.site_name, kind: '设备', cpu: d.cpu, mem: d.memory, disk: undefined, to: '/network-devices' })),
                    ].sort((a, b) => Math.max(b.cpu || 0, b.mem || 0, b.disk || 0) - Math.max(a.cpu || 0, a.mem || 0, a.disk || 0))
                      .slice(0, 6).map((r) => (
                        <li key={r.key} onClick={() => navigate(r.to)}>
                          <span className="kind">{r.kind}</span>
                          <span className="name" title={r.site || ''}>{r.name}</span>
                          {(['cpu', 'mem', 'disk'] as const).map((k) => {
                            const v = r[k];
                            return <span key={k} className={`metric ${v != null && v >= 85 ? 'hot' : ''}`}>{k === 'cpu' ? 'CPU' : k === 'mem' ? '内存' : '磁盘'} {v == null ? '-' : `${Math.round(v)}%`}</span>;
                          })}
                        </li>
                      ))}
                  </ul>
                )}
              </Panel>
              <Panel title="网络设备构成" icon={<GlobalOutlined />}>
                {(dist?.device_types || []).length === 0 ? <div className="sp-empty">暂无网络设备</div> : (
                  <div className="sp-donut">
                    <ResponsiveContainer width="45%" height={150}>
                      <PieChart>
                        <Pie data={(dist?.vendors || []).map((v) => ({ ...v, label: VENDOR_LABELS[v.name] || v.name }))} dataKey="value" nameKey="label" innerRadius={36} outerRadius={58} stroke="none">
                          {(dist?.vendors || []).map((v, i) => <Cell key={v.name} fill={PALETTE[i % PALETTE.length]} />)}
                        </Pie>
                        <Tooltip />
                      </PieChart>
                    </ResponsiveContainer>
                    <ul className="sp-legend">
                      {(dist?.device_types || []).map((t, i) => (
                        <li key={t.name} onClick={() => navigate('/network-devices')}>
                          <i style={{ background: PALETTE[i % PALETTE.length] }} />{DEVICE_LABELS[t.name] || t.name}
                          <b>{t.online}/{t.value}</b>
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
              </Panel>
              <Panel title="告警类型 Top（7 天）" icon={<AlertOutlined />}>
                {(dist?.event_types || []).length === 0 ? <div className="sp-empty">近 7 天无告警</div> : (
                  <ResponsiveContainer width="100%" height={150}>
                    <BarChart layout="vertical" data={(dist?.event_types || []).map((e) => ({ ...e, label: EVENT_LABELS[e.name] || e.name }))}
                      margin={{ top: 0, right: 24, left: 0, bottom: 0 }}>
                      <XAxis type="number" hide />
                      <YAxis type="category" dataKey="label" width={84} tick={{ fill: '#9cc3ea', fontSize: 12 }} axisLine={false} tickLine={false} />
                      <Tooltip cursor={{ fill: 'rgba(32,212,255,0.06)' }} contentStyle={{ background: '#0c1c36', border: '1px solid #1d4a7a' }} />
                      <Bar dataKey="value" name="次数" radius={[0, 6, 6, 0]} barSize={12} label={{ position: 'right', fill: '#cfe6ff', fontSize: 11 }}>
                        {(dist?.event_types || []).map((e, i) => <Cell key={e.name} fill={PALETTE[i % PALETTE.length]} />)}
                      </Bar>
                    </BarChart>
                  </ResponsiveContainer>
                )}
              </Panel>
            </section>
          </>
        )}
        <SiteDetailDrawer siteId={selectedSite} onClose={closeSite}
          getContainer={fullscreen && screenRef.current ? () => screenRef.current! : undefined} />
      </div>
    </ConfigProvider>
  );
}

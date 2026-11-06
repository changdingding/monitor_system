import { useMemo, useState } from 'react';
import type { DashboardSite } from '../../types';
import { SITE_TYPE_LABELS } from '../../types';

/**
 * 站点连接拓扑：以总部（或监控中心）为圆心，按上下级关系径向展开。
 * 连线表示站点与上级 / 总部的数据链路：流动的光点 = 采集器正常上报；红色虚线 = 失联。
 */

interface Props {
  sites: DashboardSite[];
  onSelect: (site: DashboardSite) => void;
  height?: number;
}

interface LayoutNode {
  key: string;
  site?: DashboardSite;
  name: string;
  depth: number;
  parent?: LayoutNode;
  children: LayoutNode[];
  leaves: number;
  angle: number;
  x: number;
  y: number;
}

const W = 1000;
const H = 600;
const CX = W / 2;
const CY = H / 2;

export const HEALTH_COLOR: Record<string, string> = {
  good: '#22e3a5',
  warn: '#ffb547',
  danger: '#ff4d6a',
};
const CLOUD_COLOR = '#4d9bff';

function nodeColor(site?: DashboardSite) {
  if (!site) return '#20d4ff';
  if (site.site_type === 'cloud') return site.health_level === 'danger' ? HEALTH_COLOR.danger : CLOUD_COLOR;
  return HEALTH_COLOR[site.health_level || 'good'];
}

function nodeRadius(node: LayoutNode) {
  if (!node.site) return 24;
  switch (node.site.site_type) {
    case 'hq': return node.depth === 0 ? 24 : 16;
    case 'branch': return 12;
    case 'cloud': return 10;
    default: return 7;
  }
}

function buildLayout(sites: DashboardSite[]) {
  const ids = new Set(sites.map((s) => s.id));
  const tops = sites.filter((s) => !s.parent_id || !ids.has(s.parent_id));
  const topHq = tops.filter((s) => s.site_type === 'hq');
  // 只有一个顶级总部时以它为圆心，其他顶级站点（未设置上级的门店 / 云端）都连到总部
  const rootSite = topHq.length === 1 ? topHq[0] : undefined;
  const root: LayoutNode = {
    key: rootSite ? `s${rootSite.id}` : 'root', site: rootSite, name: rootSite?.name || '监控中心',
    depth: 0, children: [], leaves: 0, angle: 0, x: CX, y: CY,
  };
  const nodes = new Map<number, LayoutNode>();
  sites.forEach((s) => {
    if (rootSite && s.id === rootSite.id) {
      nodes.set(s.id, root);
      return;
    }
    nodes.set(s.id, { key: `s${s.id}`, site: s, name: s.name, depth: 1, children: [], leaves: 0, angle: 0, x: 0, y: 0 });
  });
  sites.forEach((s) => {
    const node = nodes.get(s.id)!;
    if (node === root) return;
    const parent = s.parent_id && ids.has(s.parent_id) ? nodes.get(s.parent_id)! : root;
    node.parent = parent;
    parent.children.push(node);
  });
  // 排序：分支 / 总部在前，问题站点相邻便于观察
  const order: Record<string, number> = { hq: 0, branch: 1, store: 2, cloud: 3 };
  const sortTree = (n: LayoutNode, depth: number) => {
    n.depth = depth;
    n.children.sort((a, b) => (order[a.site?.site_type || 'store'] - order[b.site?.site_type || 'store'])
      || a.name.localeCompare(b.name, 'zh'));
    n.children.forEach((c) => sortTree(c, depth + 1));
    n.leaves = n.children.length ? n.children.reduce((sum, c) => sum + c.leaves, 0) : 1;
  };
  sortTree(root, 0);
  const all: LayoutNode[] = [];
  let maxDepth = 1;
  const walk = (n: LayoutNode) => { all.push(n); maxDepth = Math.max(maxDepth, n.depth); n.children.forEach(walk); };
  walk(root);

  // 径向布局：角度按叶子数分配，半径按层级等分；横向拉伸成椭圆以铺满宽屏
  const place = (n: LayoutNode, start: number, end: number) => {
    n.angle = (start + end) / 2;
    if (n.depth > 0) {
      const ratio = maxDepth === 1 ? 0.78 : 0.34 + (0.62 * (n.depth - 1)) / Math.max(1, maxDepth - 1);
      const r = Math.min(CX, CY) * ratio;
      n.x = CX + Math.cos(n.angle) * r * 1.6;
      n.y = CY + Math.sin(n.angle) * r * 0.92;
    }
    let cursor = start;
    n.children.forEach((c) => {
      const span = ((end - start) * c.leaves) / Math.max(1, n.leaves);
      place(c, cursor, cursor + span);
      cursor += span;
    });
  };
  place(root, -Math.PI / 2, Math.PI * 1.5);
  return { root, all };
}

export default function TopologyMap({ sites, onSelect, height = 460 }: Props) {
  const [hover, setHover] = useState<LayoutNode | null>(null);
  const { all } = useMemo(() => buildLayout(sites), [sites]);
  const dense = all.length > 40;

  const edges = all.filter((n) => n.parent);
  const showLabel = (n: LayoutNode) =>
    !dense || n.depth <= 1 || n.site?.site_type !== 'store' || n.site?.health_level !== 'good' || hover === n;

  return (
    <div className="topo-wrap" style={{ height }}>
      <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="xMidYMid meet" className="topo-svg">
        <defs>
          <radialGradient id="topo-core" cx="50%" cy="50%" r="50%">
            <stop offset="0%" stopColor="#5ff4ff" stopOpacity="0.95" />
            <stop offset="60%" stopColor="#0f8fd6" stopOpacity="0.6" />
            <stop offset="100%" stopColor="#0f8fd6" stopOpacity="0" />
          </radialGradient>
          <filter id="topo-glow" x="-50%" y="-50%" width="200%" height="200%">
            <feGaussianBlur stdDeviation="3" result="b" />
            <feMerge><feMergeNode in="b" /><feMergeNode in="SourceGraphic" /></feMerge>
          </filter>
        </defs>

        {/* 背景同心椭圆 */}
        {[0.34, 0.62, 0.96].map((r) => (
          <ellipse key={r} cx={CX} cy={CY} rx={Math.min(CX, CY) * r * 1.6} ry={Math.min(CX, CY) * r * 0.92}
            className="topo-orbit" />
        ))}

        {edges.map((n) => {
          const p = n.parent!;
          const mx = (p.x + n.x) / 2 + (CY - (p.y + n.y) / 2) * 0.08;
          const my = (p.y + n.y) / 2 - (CX - (p.x + n.x) / 2) * 0.04;
          const path = `M${p.x},${p.y} Q${mx},${my} ${n.x},${n.y}`;
          const down = n.site?.probe_down;
          const cloud = n.site?.site_type === 'cloud';
          const color = down ? HEALTH_COLOR.danger : cloud ? CLOUD_COLOR : nodeColor(n.site);
          const active = hover && (hover === n || hover === p);
          return (
            <g key={`e-${n.key}`} opacity={hover && !active ? 0.25 : 1}>
              <path d={path} className="topo-edge" stroke={color} strokeOpacity={down ? 0.75 : 0.35}
                strokeDasharray={down || cloud ? '5 6' : undefined} />
              {!down && <path d={path} className="topo-flow" stroke={color} style={{ color }} />}
            </g>
          );
        })}

        {all.map((n) => {
          const r = nodeRadius(n);
          const color = nodeColor(n.site);
          const danger = n.site?.health_level === 'danger';
          const isRoot = n.depth === 0;
          return (
            <g key={n.key} transform={`translate(${n.x},${n.y})`} className="topo-node"
              onMouseEnter={() => setHover(n)} onMouseLeave={() => setHover(null)}
              onClick={() => n.site && onSelect(n.site)} style={{ cursor: n.site ? 'pointer' : 'default' }}>
              {isRoot && (
                <>
                  <circle r={r * 2.6} fill="url(#topo-core)" opacity={0.55} />
                  <circle r={r * 1.9} className="topo-ring spin" />
                  <circle r={r * 1.45} className="topo-ring spin-rev" />
                </>
              )}
              {danger && <circle r={r + 4} className="topo-pulse" stroke={color} />}
              <circle r={r} fill={isRoot && !n.site ? '#0b3a5c' : '#0a1d36'} stroke={color} strokeWidth={isRoot ? 3 : 2}
                filter="url(#topo-glow)" />
              <circle r={Math.max(2.5, r * 0.42)} fill={color} />
              {showLabel(n) && (
                <text y={r + 14} textAnchor="middle" className={isRoot ? 'topo-label root' : 'topo-label'}>
                  {n.name.length > 10 ? `${n.name.slice(0, 10)}…` : n.name}
                </text>
              )}
            </g>
          );
        })}
      </svg>

      {hover && (
        <div className="topo-tip" style={{ left: `${(hover.x / W) * 100}%`, top: `${(hover.y / H) * 100}%` }}>
          <div className="topo-tip-title">{hover.name}</div>
          {hover.site ? (
            <>
              <div>{SITE_TYPE_LABELS[hover.site.site_type]}{hover.site.city ? ` · ${hover.site.city}` : ''}</div>
              <div>健康分 <b style={{ color: nodeColor(hover.site) }}>{hover.site.health_score ?? '-'}</b>
                {hover.site.probe_down ? ' · 采集器失联' : ''}</div>
              <div>服务器 {hover.site.server_online}/{hover.site.server_total} · 设备 {hover.site.device_online}/{hover.site.device_total}</div>
              <div>未恢复告警 {hover.site.active_alerts}{hover.site.if_down ? ` · 断开端口 ${hover.site.if_down}` : ''}</div>
              <div className="topo-tip-hint">点击查看详情</div>
            </>
          ) : <div>{all.length - 1} 个站点接入</div>}
        </div>
      )}
      {sites.length === 0 && <div className="topo-empty">还没有站点。请先在「站点 / 分支」中建立总部、分支和门店。</div>}
    </div>
  );
}

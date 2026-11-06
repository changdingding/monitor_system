import type { Site } from '../types';
import { SITE_TYPE_LABELS } from '../types';

type SiteNode = Site & { children?: SiteNode[] };

/** 站点列表 → 树（上级不在可见范围内的站点作为根） */
export function buildSiteTree(sites: Site[]): SiteNode[] {
  const map = new Map<number, SiteNode>();
  sites.forEach((s) => map.set(s.id, { ...s }));
  const roots: SiteNode[] = [];
  map.forEach((node) => {
    const parent = node.parent_id ? map.get(node.parent_id) : undefined;
    if (parent) (parent.children ||= []).push(node);
    else roots.push(node);
  });
  return roots;
}

export function siteTreeData(sites: Site[], disabled?: (s: Site) => boolean): any[] {
  const toNode = (n: SiteNode): any => ({
    title: `${n.name}（${SITE_TYPE_LABELS[n.site_type]}）`,
    value: n.id,
    key: n.id,
    disabled: disabled?.(n),
    children: (n.children || []).map(toNode),
  });
  return buildSiteTree(sites).map(toNode);
}


import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Button, Checkbox, Drawer, Empty, Input, Select, Tabs, Tag } from 'antd';
import {
  CloseCircleOutlined,
  DeleteOutlined,
  ExportOutlined,
  FileTextOutlined,
  ImportOutlined,
  LinkOutlined,
  PlusOutlined,
  ShareAltOutlined,
} from '@ant-design/icons';
import type { TripWorkspaceState } from './TripWorkspace';

interface ChecklistItem {
  id: string;
  text: string;
  completed: boolean;
  day?: number;
  activity_id?: string;
  poi_id?: string;
}

interface TripNote {
  id: string;
  content: string;
  target_type: 'trip' | 'day' | 'activity' | 'poi';
  target_id?: string;
  day?: number;
  activity_id?: string;
  poi_id?: string;
}

interface TripTemplate {
  id: string;
  name: string;
  audience: string[];
  budget_level: string;
  pace: string;
  default_days: number;
  preferences: string[];
  prompt: string;
}

export const buildChecklistItem = (
  id: string,
  text: string,
  binding: string,
  workspace: TripWorkspaceState,
): ChecklistItem => {
  const [targetType, targetId] = binding.split(':', 2);
  const linkedActivity = (workspace.days || []).flatMap((day) => day.activities)
    .find((activity) => activity.id === targetId);
  return {
    id,
    text,
    completed: false,
    day: targetType === 'day' ? Number(targetId) : linkedActivity?.day,
    activity_id: targetType === 'activity' ? targetId : undefined,
    poi_id: targetType === 'poi' ? targetId : undefined,
  };
};

export const buildTripNote = (
  id: string,
  content: string,
  targetType: TripNote['target_type'],
  targetId: string,
  workspace: TripWorkspaceState,
): TripNote => {
  const linkedActivity = (workspace.days || []).flatMap((day) => day.activities)
    .find((activity) => activity.id === targetId);
  return {
    id,
    content,
    target_type: targetType,
    target_id: targetType === 'trip' ? undefined : targetId,
    day: targetType === 'day' ? Number(targetId) : linkedActivity?.day,
    activity_id: targetType === 'activity' ? targetId : undefined,
    poi_id: targetType === 'poi' ? targetId : undefined,
  };
};

interface Props {
  plan: Record<string, unknown> | null;
  document?: Record<string, unknown> | null;
  workspace: TripWorkspaceState;
  onUseTemplate?: (prompt: string) => void;
  onImportDocument?: (document: Record<string, unknown>) => void;
  onDocumentChange?: (document: Record<string, unknown>) => void;
}

const SHARE_SCOPES = [
  { label: '行程', value: 'itinerary' },
  { label: '预算', value: 'budget' },
  { label: '来源', value: 'sources' },
  { label: '清单', value: 'checklist' },
  { label: '便签', value: 'notes' },
];

const downloadText = (filename: string, content: string, type: string) => {
  const url = URL.createObjectURL(new Blob([content], { type }));
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  URL.revokeObjectURL(url);
};

const readStored = <T,>(key: string, fallback: T): T => {
  try {
    const value = localStorage.getItem(key);
    return value ? JSON.parse(value) as T : fallback;
  } catch {
    return fallback;
  }
};

export const buildEffectiveTripPlan = (
  plan: Record<string, unknown> | null,
  workspace: TripWorkspaceState,
): Record<string, unknown> | null => {
  if (plan && typeof plan === 'object') return plan;
  const days = workspace.days || [];
  if (days.length === 0) return null;
  const activities = days.flatMap((day) => day.activities.map((activity) => ({
    ...activity,
    activity_id: activity.id,
    place: activity.place ? { ...activity.place } : undefined,
  })));
  return {
    plan_id: 'workspace-restored-draft',
    version: 1,
    title: '恢复的旅行规划',
    intent: { days: days.length },
    days: days.length,
    trip_days: days,
    activities,
    budget_summary: workspace.budget || {},
    map_locations: workspace.locations,
    source_references: workspace.sources,
    warnings: ['该方案由历史工作台恢复，无法确认的实时字段保持待确认状态'],
  };
};

const TripProductTools: React.FC<Props> = ({ plan, document, workspace, onUseTemplate, onImportDocument, onDocumentChange }) => {
  const effectivePlan = useMemo(() => buildEffectiveTripPlan(plan, workspace), [plan, workspace]);
  const planId = String(effectivePlan?.plan_id || 'draft');
  const storageKey = `trip_product_${planId}`;
  const initial = readStored<{ checklist: ChecklistItem[]; notes: TripNote[] }>(storageKey, { checklist: [], notes: [] });
  const [open, setOpen] = useState(false);
  const [templates, setTemplates] = useState<TripTemplate[]>([]);
  const [checklist, setChecklist] = useState<ChecklistItem[]>(initial.checklist);
  const [notes, setNotes] = useState<TripNote[]>(initial.notes);
  const [newChecklist, setNewChecklist] = useState('');
  const [checklistBinding, setChecklistBinding] = useState('trip');
  const [newNote, setNewNote] = useState('');
  const [noteTarget, setNoteTarget] = useState<TripNote['target_type']>('trip');
  const [noteTargetId, setNoteTargetId] = useState('');
  const [shareScopes, setShareScopes] = useState<string[]>(['itinerary']);
  const [shareLink, setShareLink] = useState('');
  const [shareStatus, setShareStatus] = useState('');
  const fileInputRef = useRef<HTMLInputElement>(null);
  const emittedDocumentSignatureRef = useRef('');

  useEffect(() => {
    const stored = readStored<{ checklist: ChecklistItem[]; notes: TripNote[] }>(storageKey, { checklist: [], notes: [] });
    const documentChecklist = Array.isArray(document?.checklist) ? document.checklist as ChecklistItem[] : [];
    const documentNotes = Array.isArray(document?.notes) ? document.notes as TripNote[] : [];
    setChecklist(documentChecklist.length > 0 ? documentChecklist : stored.checklist);
    setNotes(documentNotes.length > 0 ? documentNotes : stored.notes);
  }, [storageKey]);

  useEffect(() => {
    localStorage.setItem(storageKey, JSON.stringify({ checklist, notes }));
  }, [checklist, notes, storageKey]);

  useEffect(() => {
    fetch('/api/trip-templates').then((response) => response.ok ? response.json() : Promise.reject())
      .then((payload) => setTemplates(Array.isArray(payload.templates) ? payload.templates : []))
      .catch(() => setTemplates([]));
  }, []);

  const documentPayload = useMemo<Record<string, any>>(() => {
    if (!document) return {
    schema_version: '1.0', plan: effectivePlan, budget: workspace.budget,
    sources: workspace.sources, checklist, notes,
    };
    const planDays = Array.isArray(effectivePlan?.trip_days)
      ? effectivePlan.trip_days
      : Array.isArray(effectivePlan?.days) ? effectivePlan.days : [];
    const workspaceDays = (workspace.days || []).map((day) => ({
      ...day,
      activities: day.activities.map((activity) => ({
        ...activity,
        activity_id: activity.id,
      })),
    }));
    const itineraryDays = planDays.length > 0 ? planDays : workspaceDays;
    const locationIds = itineraryDays.flatMap((day: any) => (
      Array.isArray(day?.activities) ? day.activities : []
    )).filter((activity: any) => activity?.map_visible !== false && activity?.place?.lat != null && activity?.place?.lng != null)
      .map((activity: any) => String(activity.place.poi_id || activity.activity_id || activity.id));
    const version = Number(effectivePlan?.version || document.version || 1);
    const title = String(effectivePlan?.title || document.title || '旅行规划');
    return {
      ...document,
      plan_id: effectivePlan?.plan_id || document.plan_id,
      version,
      title,
      intent: effectivePlan?.intent || document.intent,
      itinerary: {
        ...(document.itinerary as Record<string, unknown> || {}),
        status: itineraryDays.length > 0 ? 'ready' : 'needs_confirmation',
        days: itineraryDays,
      },
      map_guidance: {
        ...(document.map_guidance as Record<string, unknown> || {}),
        location_ids: locationIds,
      },
      delivery: {
        ...(document.delivery as Record<string, unknown> || {}),
        markdown_filename: `${title.replace(/[\\/:*?"<>|]+/g, '-')}-v${version}.md`,
      },
      budget: workspace.budget || document.budget || {},
      sources: workspace.sources.length > 0 ? workspace.sources : document.sources || [],
      checklist,
      notes,
    };
  }, [checklist, document, effectivePlan, notes, workspace.budget, workspace.days, workspace.sources]);

  useEffect(() => {
    if (!document || !onDocumentChange) return;
    const signature = JSON.stringify({
      planId: documentPayload.plan_id,
      version: documentPayload.version,
      itinerary: documentPayload.itinerary,
      mapGuidance: documentPayload.map_guidance,
      checklist,
      notes,
      delivery: documentPayload.delivery,
    });
    if (signature === emittedDocumentSignatureRef.current) return;
    emittedDocumentSignatureRef.current = signature;
    onDocumentChange(documentPayload as Record<string, unknown>);
  }, [checklist, document, documentPayload, notes, onDocumentChange]);

  const addChecklist = () => {
    const text = newChecklist.trim();
    if (!text) return;
    setChecklist((items) => [...items, buildChecklistItem(crypto.randomUUID(), text, checklistBinding, workspace)]);
    setNewChecklist('');
  };

  const addNote = () => {
    const content = newNote.trim();
    if (!content) return;
    if (noteTarget !== 'trip' && !noteTargetId) return;
    setNotes((items) => [...items, buildTripNote(crypto.randomUUID(), content, noteTarget, noteTargetId, workspace)]);
    setNewNote('');
  };

  const exportJson = async () => {
    setShareStatus('正在生成 JSON');
    const response = await fetch('/api/trips/import', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ format: 'json', content: documentPayload }),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload?.detail?.message || '导出失败');
    const filename = String(payload?.delivery?.markdown_filename || 'trip-plan.md').replace(/\.md$/i, '.json');
    downloadText(filename, JSON.stringify(payload, null, 2), 'application/json;charset=utf-8');
    setShareStatus('JSON 已导出');
  };
  const exportMarkdown = async () => {
    setShareStatus('正在生成 Markdown');
    const response = await fetch('/api/trips/export/markdown', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ document: documentPayload }),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload?.detail?.message || '导出失败');
    downloadText(payload.filename || 'trip-plan.md', payload.content, 'text/markdown;charset=utf-8');
    setShareStatus('Markdown 已导出');
  };

  const importFile = async (file: File) => {
    const format = file.name.toLowerCase().endsWith('.md') ? 'markdown' : 'json';
    const response = await fetch('/api/trips/import', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ format, content: await file.text() }),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload?.detail?.message || '导入失败');
    setChecklist(Array.isArray(payload.checklist) ? payload.checklist : []);
    setNotes(Array.isArray(payload.notes) ? payload.notes : []);
    onImportDocument?.(payload);
    setShareStatus('导入完成，已重新执行结构校验');
  };

  const createShare = async () => {
    if (!effectivePlan || shareScopes.length === 0) return;
    setShareStatus('正在创建只读分享');
    const response = await fetch('/api/shares', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ document: documentPayload, scopes: shareScopes }),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload?.detail?.message || '创建分享失败');
    const link = `${window.location.origin}/share/${payload.token}`;
    localStorage.setItem(`trip_share_${planId}`, JSON.stringify({ token: payload.token, managementKey: payload.management_key, link }));
    setShareLink(link);
    setShareStatus('只读分享已创建');
    if (document && onDocumentChange) {
      onDocumentChange({ ...documentPayload, delivery: {
        ...(document.delivery as Record<string, unknown> || {}), share_status: 'shared', share_scopes: shareScopes,
      }});
    }
  };

  const closeShare = async () => {
    const saved = readStored<{ token?: string; managementKey?: string }>(`trip_share_${planId}`, {});
    if (!saved.token || !saved.managementKey) return;
    const response = await fetch(`/api/shares/${saved.token}`, { method: 'DELETE', headers: { 'X-Share-Management-Key': saved.managementKey } });
    if (!response.ok) throw new Error('关闭分享失败');
    localStorage.removeItem(`trip_share_${planId}`);
    setShareLink('');
    setShareStatus('分享已关闭');
    if (document && onDocumentChange) {
      onDocumentChange({ ...documentPayload, delivery: {
        ...(document.delivery as Record<string, unknown> || {}), share_status: 'closed', share_scopes: [],
      }});
    }
  };

  const run = (action: () => Promise<void>) => void action().catch((error) => setShareStatus(error instanceof Error ? error.message : '操作失败'));
  const dayOptions = (workspace.days || []).map((day) => ({ label: `Day ${day.day}`, value: String(day.day) }));
  const activityOptions = (workspace.days || []).flatMap((day) => day.activities.map((activity) => ({
    label: `Day ${day.day} · ${activity.title || activity.place?.name || '未命名活动'}`,
    value: activity.id,
  })));
  const poiOptions = workspace.locations.map((location) => ({ label: location.name, value: location.id }));
  const checklistOptions = [
    { label: '整个行程', value: 'trip' },
    ...dayOptions.map((option) => ({ ...option, value: `day:${option.value}` })),
    ...activityOptions.map((option) => ({ ...option, value: `activity:${option.value}` })),
    ...poiOptions.map((option) => ({ ...option, value: `poi:${option.value}` })),
  ];
  const noteOptions = noteTarget === 'day' ? dayOptions : noteTarget === 'activity' ? activityOptions : poiOptions;
  const bindingLabel = (item: ChecklistItem) => {
    if (item.activity_id) return activityOptions.find((option) => option.value === item.activity_id)?.label || '活动待确认';
    if (item.poi_id) return poiOptions.find((option) => option.value === item.poi_id)?.label || '地点待确认';
    return item.day ? `Day ${item.day}` : '全程';
  };

  return (
    <>
      <Button size="small" icon={<FileTextOutlined />} onClick={() => setOpen(true)}>行程工具</Button>
      <Drawer title="行程工具" width={520} open={open} onClose={() => setOpen(false)} destroyOnClose={false}>
        <Tabs items={[
          {
            key: 'templates', label: '模板', children: templates.length ? (
              <div className="trip-template-list">{templates.map((template) => (
                <div className="trip-template-item" key={template.id}>
                  <div><strong>{template.name}</strong><span>{template.default_days} 天 · {template.budget_level}预算 · {template.pace}</span></div>
                  <p>适用：{template.audience.join('、')}</p>
                  <div>{template.preferences.map((item) => <Tag key={item}>{item}</Tag>)}</div>
                  <Button type="primary" size="small" onClick={() => { onUseTemplate?.(template.prompt); setOpen(false); }}>使用并继续澄清</Button>
                </div>
              ))}</div>
            ) : <Empty description="模板暂不可用" />,
          },
          {
            key: 'checklist', label: '清单', children: <div className="trip-user-data-panel">
              <div className="trip-inline-form"><Input value={newChecklist} onChange={(event) => setNewChecklist(event.target.value)} placeholder="新增待办事项" onPressEnter={addChecklist} /><Select aria-label="清单关联对象" options={checklistOptions} value={checklistBinding} onChange={setChecklistBinding} /><Button icon={<PlusOutlined />} onClick={addChecklist} aria-label="新增清单" /></div>
              {checklist.map((item) => <div className="trip-user-data-row" key={item.id}><Checkbox checked={item.completed} onChange={(event) => setChecklist((items) => items.map((value) => value.id === item.id ? { ...value, completed: event.target.checked } : value))}>{item.text}</Checkbox><span>{bindingLabel(item)}</span><Button danger type="text" icon={<DeleteOutlined />} onClick={() => setChecklist((items) => items.filter((value) => value.id !== item.id))} aria-label={`删除${item.text}`} /></div>)}
            </div>,
          },
          {
            key: 'notes', label: '便签', children: <div className="trip-user-data-panel">
              <div className="trip-inline-form"><Input.TextArea value={newNote} onChange={(event) => setNewNote(event.target.value)} placeholder="添加用户备注" autoSize={{ minRows: 2, maxRows: 4 }} /><Select aria-label="便签目标类型" value={noteTarget} onChange={(value) => { setNoteTarget(value); setNoteTargetId(''); }} options={[{ label: '整个行程', value: 'trip' }, { label: '某一天', value: 'day' }, { label: '活动', value: 'activity' }, { label: '地点', value: 'poi' }]} />{noteTarget !== 'trip' && <Select aria-label="便签关联对象" placeholder="选择关联对象" options={noteOptions} value={noteTargetId || undefined} onChange={setNoteTargetId} />}<Button icon={<PlusOutlined />} onClick={addNote} disabled={noteTarget !== 'trip' && !noteTargetId}>添加</Button></div>
              {notes.map((note) => <div className="trip-user-data-row" key={note.id}><span><Tag>{note.target_type}</Tag>{note.content}</span><Button danger type="text" icon={<DeleteOutlined />} onClick={() => setNotes((items) => items.filter((value) => value.id !== note.id))} aria-label={`删除便签${note.content}`} /></div>)}
            </div>,
          },
          {
            key: 'data', label: '导入导出', children: <div className="trip-data-actions">
              <Button icon={<ImportOutlined />} onClick={() => fileInputRef.current?.click()}>导入 JSON/Markdown</Button>
              <input ref={fileInputRef} hidden type="file" accept=".json,.md,.markdown,application/json,text/markdown" onChange={(event) => { const file = event.target.files?.[0]; if (file) run(() => importFile(file)); event.currentTarget.value = ''; }} />
              <Button icon={<ExportOutlined />} disabled={!effectivePlan} onClick={() => run(exportJson)}>导出 JSON</Button>
              <Button icon={<ExportOutlined />} disabled={!effectivePlan} onClick={() => run(exportMarkdown)}>导出 Markdown</Button>
              <p>外部文件导入后会重新校验，无法确认的字段会标记为待确认。</p>
            </div>,
          },
          {
            key: 'share', label: '分享', children: <div className="trip-share-panel">
              <p>行程默认私有。选择公开范围后创建只读快照。</p>
              <Checkbox.Group options={SHARE_SCOPES} value={shareScopes} onChange={(values) => setShareScopes(values.map(String))} />
              <div className="trip-share-actions"><Button type="primary" icon={<ShareAltOutlined />} disabled={!effectivePlan || shareScopes.length === 0} onClick={() => run(createShare)}>创建只读分享</Button><Button danger icon={<CloseCircleOutlined />} onClick={() => run(closeShare)}>关闭分享</Button></div>
              {shareLink && <a href={shareLink} target="_blank" rel="noreferrer"><LinkOutlined /> {shareLink}</a>}
              {shareStatus && <p role="status">{shareStatus}</p>}
            </div>,
          },
        ]} />
      </Drawer>
    </>
  );
};

export default TripProductTools;

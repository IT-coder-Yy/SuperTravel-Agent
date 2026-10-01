import { useState } from 'react';
import { Alert, Button, Input, Modal, Select, Space } from 'antd';

export type WorkspaceEditTarget = {
  type: 'add_activity' | 'replace_activity' | 'update_activity_time' | 'update_trip_note' | 'update_day_note';
  activityId?: string;
  day?: number;
  start?: string;
  end?: string;
  content?: string;
  title: string;
};

export default function TripWorkspaceEditor({ target, destination, onClose, onSave }: {
  target: WorkspaceEditTarget;
  destination: string;
  onClose: () => void;
  onSave: (type: string, payload: Record<string, unknown>) => void | boolean | Promise<void | boolean>;
}) {
  const [query, setQuery] = useState('');
  const [kind, setKind] = useState('attraction');
  const [results, setResults] = useState<Array<{ selection_id: string; place: { name: string; address?: string } }>>([]);
  const [selection, setSelection] = useState('');
  const [start, setStart] = useState(target.start || '09:00');
  const [end, setEnd] = useState(target.end || '10:00');
  const [content, setContent] = useState(target.content || '');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const places = target.type === 'add_activity' || target.type === 'replace_activity';
  const times = target.type === 'add_activity' || target.type === 'update_activity_time';
  const notes = target.type === 'update_trip_note' || target.type === 'update_day_note';
  const search = async () => {
    if (query.trim().length < 2 || busy) return;
    setBusy(true); setError(''); setSelection(''); setResults([]);
    try {
      const params = new URLSearchParams({ q: query.trim(), destination, kind });
      const response = await fetch(`/api/places/search?${params}`);
      if (!response.ok) throw new Error('地点搜索暂时不可用，请稍后重试。');
      const body = await response.json();
      setResults(body.items || []);
      if (!body.items?.length) setError('没有找到可核验的地点，请补充名称或地址后重试。');
    } catch (cause) { setError(cause instanceof Error ? cause.message : '地点搜索失败'); }
    finally { setBusy(false); }
  };
  const save = async () => {
    if (times && (!/^\d{2}:(00|15|30|45)$/.test(start) || !/^\d{2}:(00|15|30|45)$/.test(end) || end <= start)) {
      setError('请选择 15 分钟粒度的时间，结束时间须晚于开始时间。'); return;
    }
    setBusy(true); setError('');
    try {
      const succeeded = await onSave(target.type, {
        ...(target.activityId ? { activity_id: target.activityId } : {}),
        ...(target.day ? { day: target.day } : {}),
        ...(places ? { selection_id: selection } : {}),
        ...(times ? { start_at: start, end_at: end } : {}),
        ...(notes ? { content } : {}),
      });
      if (succeeded !== false) onClose();
      else setError('未能保存，请检查提示后重试。');
    } catch { setError('保存失败，输入已保留，请重试。'); }
    finally { setBusy(false); }
  };
  return <Modal open title={target.title} onCancel={onClose} onOk={() => { void save(); }}
    okText="保存到草稿" cancelText="取消" confirmLoading={busy}
    okButtonProps={{ disabled: places && !selection }} maskClosable={!busy}>
    <Space direction="vertical" style={{ width: '100%' }} size="middle">
      {places && <>
        <Select aria-label="地点类型" value={kind} onChange={(value) => { setKind(value); setSelection(''); setResults([]); }} options={[
          { value: 'attraction', label: '景点' }, { value: 'food', label: '餐饮' }, { value: 'shopping', label: '购物' },
        ]} />
        <Input.Search aria-label="搜索真实地点" value={query} onChange={(event) => setQuery(event.target.value)}
          placeholder={`搜索${destination}的地点名称或地址`} onSearch={() => { void search(); }} loading={busy} enterButton="搜索" />
        {results.map((result) => <Button key={result.selection_id} block type={selection === result.selection_id ? 'primary' : 'default'}
          onClick={() => setSelection(result.selection_id)} style={{ height: 'auto', whiteSpace: 'normal', textAlign: 'left' }}>
          {result.place.name}{result.place.address ? ` · ${result.place.address}` : ''}
        </Button>)}
      </>}
      {times && <Space wrap>
        <label>开始时间<Input type="time" aria-label="开始时间" step={900} value={start} onChange={(event) => setStart(event.target.value)} /></label>
        <label>结束时间<Input type="time" aria-label="结束时间" step={900} value={end} onChange={(event) => setEnd(event.target.value)} /></label>
      </Space>}
      {notes && <Input.TextArea aria-label={target.title} value={content} onChange={(event) => setContent(event.target.value)} maxLength={2000} showCount autoSize={{ minRows: 3, maxRows: 8 }} />}
      {error && <Alert type="warning" message={error} showIcon />}
    </Space>
  </Modal>;
}

import { PlayCircleOutlined } from '@ant-design/icons';
import './DemoCaseGallery.css';

export interface DemoReplayCase {
  id: string;
  title: string;
  summary: string;
  meta: string;
  recordingStatus: 'recording' | 'ready';
  eventCount?: number;
  notice?: string;
  template?: {
    origin: string;
    destination: string;
    days: number;
    adults: number;
    children: number;
    seniors: number;
    preferences: string[];
  };
}

export interface DemoCaseGalleryProps {
  cases?: DemoReplayCase[];
  onReplay?: (caseId: string) => void;
}

export const plannedDemoCases: DemoReplayCase[] = [
  {
    id: 'hangzhou-humanities-3d',
    title: '杭州三日人文慢游',
    summary: '湖山、人文与本地餐饮结合的轻松路线。',
    meta: '3 天 · 双人 · 上海出发',
    recordingStatus: 'recording',
  },
  {
    id: 'beijing-family-3d',
    title: '北京亲子文化三日',
    summary: '兼顾历史场馆、亲子节奏与市内交通。',
    meta: '3 天 · 家庭 · 文化体验',
    recordingStatus: 'recording',
  },
  {
    id: 'chengdu-food-3d',
    title: '成都美食慢游三日',
    summary: '围绕街区、美食与休闲体验组织每日动线。',
    meta: '3 天 · 朋友同行 · 重庆出发',
    recordingStatus: 'recording',
  },
];

const DemoCaseGallery = ({ cases = plannedDemoCases, onReplay }: DemoCaseGalleryProps) => (
  <section className="demo-case-gallery" aria-labelledby="demo-case-gallery-title">
    <p id="demo-case-gallery-title" className="demo-case-gallery-title">
      点选示例，一键体验完整链路。
    </p>
    <div className="demo-case-list">
      {cases.map((demoCase) => {
        const ready = demoCase.recordingStatus === 'ready';
        return (
          <button
            key={demoCase.id}
            type="button"
            className="demo-case-card"
            disabled={!ready}
            aria-label={`${demoCase.title}${ready ? '，开始示例回放' : '，真实案例录制中'}`}
            onClick={() => ready && onReplay?.(demoCase.id)}
          >
            <span className="demo-case-card-copy">
              <strong>{demoCase.title}</strong>
              <span>{demoCase.summary}</span>
              <small>{demoCase.meta}</small>
            </span>
            <span className={`demo-case-status ${ready ? 'demo-case-status-ready' : ''}`}>
              {ready && <PlayCircleOutlined aria-hidden="true" />}
              {ready ? '一键回放' : '录制中'}
            </span>
          </button>
        );
      })}
    </div>
  </section>
);

export default DemoCaseGallery;

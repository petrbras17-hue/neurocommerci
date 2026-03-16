// Channel Map v4 — константы и конфигурация
// Dark Terminal theme: #0a0a0b bg, #00ff88 accent

export const TILE_URL =
  'https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png';

export const TILE_ATTRIBUTION =
  '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors &copy; <a href="https://carto.com/attributions">CARTO</a>';

export const MAP_CONFIG = {
  center: [30, 30] as [number, number],
  zoom: 3,
  minZoom: 2,
  maxZoom: 18,
  maxBoundsViscosity: 1.0,
};

export const ZOOM_LEVELS = {
  WORLD: 3,   // Кластеры по странам
  REGION: 6,  // Кластеры по городам
  CITY: 10,   // Отдельные каналы (точки)
  DETAIL: 14, // Аватары каналов
};

// Цвета для каждой категории — Dark Terminal palette
export const CATEGORY_COLORS: Record<string, string> = {
  // Основные
  tech: '#00ff88',
  crypto: '#f7931a',
  news: '#3b82f6',
  entertainment: '#ec4899',
  education: '#8b5cf6',
  business: '#f59e0b',
  lifestyle: '#06b6d4',
  politics: '#ef4444',
  gaming: '#10b981',
  science: '#6366f1',

  // Расширенные категории
  finance: '#f59e0b',
  marketing: '#84cc16',
  health: '#22d3ee',
  travel: '#fb7185',
  food: '#fbbf24',
  sport: '#4ade80',
  art: '#e879f9',
  music: '#818cf8',
  fashion: '#f472b6',
  auto: '#94a3b8',
  humor: '#facc15',
  religion: '#a78bfa',
  nature: '#34d399',
  family: '#fb923c',
  career: '#60a5fa',
  language: '#c084fc',
  realestate: '#f87171',
  legal: '#64748b',
  media: '#38bdf8',
  psychology: '#d946ef',
  parenting: '#fdba74',

  // Фолбэк
  other: '#6b7280',
};

// Метки для отображения категорий на русском
export const CATEGORY_LABELS: Record<string, string> = {
  tech: 'Технологии',
  crypto: 'Крипто',
  news: 'Новости',
  entertainment: 'Развлечения',
  education: 'Образование',
  business: 'Бизнес',
  lifestyle: 'Лайфстайл',
  politics: 'Политика',
  gaming: 'Игры',
  science: 'Наука',
  finance: 'Финансы',
  marketing: 'Маркетинг',
  health: 'Здоровье',
  travel: 'Путешествия',
  food: 'Еда',
  sport: 'Спорт',
  art: 'Искусство',
  music: 'Музыка',
  fashion: 'Мода',
  auto: 'Авто',
  humor: 'Юмор',
  religion: 'Религия',
  nature: 'Природа',
  family: 'Семья',
  career: 'Карьера',
  language: 'Языки',
  realestate: 'Недвижимость',
  legal: 'Право',
  media: 'Медиа',
  psychology: 'Психология',
  parenting: 'Родительство',
  other: 'Другое',
};

// Уровни по количеству подписчиков для фильтрации
export const SUBSCRIBER_TIERS = [
  { label: 'Все', min: 0 },
  { label: '1K+', min: 1_000 },
  { label: '5K+', min: 5_000 },
  { label: '10K+', min: 10_000 },
  { label: '50K+', min: 50_000 },
  { label: '100K+', min: 100_000 },
  { label: '1M+', min: 1_000_000 },
];

// Размеры маркеров (px)
export const MARKER_SIZES = {
  DOT_SMALL: 6,     // zoom < 14
  DOT_LARGE: 10,    // zoom 10-13 с hover
  AVATAR: 32,       // zoom 14+ — аватар
  AVATAR_LARGE: 48, // zoom 16+ — большой аватар
};

// Размеры кластеров (px) по количеству каналов
export const CLUSTER_SIZES = {
  SMALL: 32,  // < 100 каналов
  MEDIUM: 44, // 100–1000 каналов
  LARGE: 56,  // > 1000 каналов
};

// Цвет по умолчанию для категории без совпадения
export const DEFAULT_CATEGORY_COLOR = CATEGORY_COLORS.other;

// Функция: получить цвет по категории (с fallback)
export function getCategoryColor(category: string | null | undefined): string {
  if (!category) return DEFAULT_CATEGORY_COLOR;
  const key = category.toLowerCase();
  return CATEGORY_COLORS[key] ?? DEFAULT_CATEGORY_COLOR;
}

// Функция: получить метку категории на русском
export function getCategoryLabel(category: string | null | undefined): string {
  if (!category) return 'Другое';
  const key = category.toLowerCase();
  return CATEGORY_LABELS[key] ?? category;
}

// Форматирование числа подписчиков
export function formatSubscribers(count: number): string {
  if (count >= 1_000_000) return `${(count / 1_000_000).toFixed(1)}M`;
  if (count >= 1_000) return `${(count / 1_000).toFixed(1)}K`;
  return String(count);
}

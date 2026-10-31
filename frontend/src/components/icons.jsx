const base = {
  fill: 'none',
  stroke: 'currentColor',
  strokeLinecap: 'round',
  strokeLinejoin: 'round',
  'aria-hidden': true,
}

export const PlusIcon = ({ size = 16 }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" strokeWidth="2" {...base}>
    <path d="M12 5v14M5 12h14" />
  </svg>
)

export const ArrowUpIcon = ({ size = 16 }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" strokeWidth="2.4" {...base}>
    <path d="M12 19V5M5 12l7-7 7 7" />
  </svg>
)

export const MenuIcon = ({ size = 18 }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" strokeWidth="2" {...base}>
    <path d="M4 6h16M4 12h16M4 18h16" />
  </svg>
)

export const CloseIcon = ({ size = 14 }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" strokeWidth="2" {...base}>
    <path d="M18 6 6 18M6 6l12 12" />
  </svg>
)

export const CopyIcon = ({ size = 14 }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" strokeWidth="2" {...base}>
    <rect x="9" y="9" width="12" height="12" rx="2" />
    <path d="M5 15V5a2 2 0 0 1 2-2h10" />
  </svg>
)

export const CheckIcon = ({ size = 14 }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" strokeWidth="2.2" {...base}>
    <path d="M20 6 9 17l-5-5" />
  </svg>
)

export const RetryIcon = ({ size = 14 }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" strokeWidth="2" {...base}>
    <path d="M3 12a9 9 0 0 1 15.5-6.3L21 8M21 3v5h-5M21 12a9 9 0 0 1-15.5 6.3L3 16M3 21v-5h5" />
  </svg>
)

export const TrashIcon = ({ size = 14 }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" strokeWidth="2" {...base}>
    <path d="M3 6h18M8 6V4h8v2M19 6l-1 14H6L5 6" />
  </svg>
)

export const SunIcon = ({ size = 16 }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" strokeWidth="2" {...base}>
    <circle cx="12" cy="12" r="4" />
    <path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" />
  </svg>
)

export const MoonIcon = ({ size = 16 }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" strokeWidth="2" {...base}>
    <path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z" />
  </svg>
)

export const FileIcon = ({ size = 14 }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" strokeWidth="2" {...base}>
    <path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z" />
    <path d="M14 3v5h5M9 13h6M9 17h4" />
  </svg>
)

export const LockIcon = ({ size = 14 }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" strokeWidth="2" {...base}>
    <rect x="5" y="11" width="14" height="10" rx="2" />
    <path d="M8 11V7a4 4 0 0 1 8 0v4" />
  </svg>
)

export const UnlockIcon = ({ size = 14 }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" strokeWidth="2" {...base}>
    <rect x="5" y="11" width="14" height="10" rx="2" />
    <path d="M8 11V7a4 4 0 0 1 7.5-1.9" />
  </svg>
)

export const PencilIcon = ({ size = 14 }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" strokeWidth="2" {...base}>
    <path d="M4 20h4L19 9l-4-4L4 16z" />
    <path d="M13.5 6.5l4 4" />
  </svg>
)

export const ExternalIcon = ({ size = 14 }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" strokeWidth="2" {...base}>
    <path d="M14 4h6v6M20 4l-9 9" />
    <path d="M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5" />
  </svg>
)

export const MemoryIcon = ({ size = 16 }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" strokeWidth="2" {...base}>
    <path d="M12 3l1.8 4.6L18.5 9l-4.7 1.4L12 15l-1.8-4.6L5.5 9l4.7-1.4z" />
    <path d="M18 15l.8 2 2.2.8-2.2.8-.8 2.2-.8-2.2-2.2-.8 2.2-.8z" />
  </svg>
)

export const SearchIcon = ({ size = 14 }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" strokeWidth="2" {...base}>
    <circle cx="11" cy="11" r="7" />
    <path d="M20 20l-4-4" />
  </svg>
)

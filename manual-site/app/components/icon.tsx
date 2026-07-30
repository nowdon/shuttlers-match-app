type IconName =
  | "book"
  | "user"
  | "admin"
  | "message"
  | "help"
  | "search"
  | "menu"
  | "arrow"
  | "racket"
  | "clipboard"
  | "card"
  | "spark"
  | "history"
  | "settings"
  | "bell"
  | "link";

type IconProps = {
  name: IconName;
  size?: number;
};

const common = {
  fill: "none",
  stroke: "currentColor",
  strokeLinecap: "round" as const,
  strokeLinejoin: "round" as const,
  strokeWidth: 1.8,
};

export function Icon({ name, size = 24 }: IconProps) {
  const paths: Record<IconName, React.ReactNode> = {
    book: (
      <>
        <path d="M4 5.5c3.2-.8 5.7-.2 8 1.7v12c-2.3-1.9-4.8-2.5-8-1.7z" />
        <path d="M20 5.5c-3.2-.8-5.7-.2-8 1.7v12c2.3-1.9 4.8-2.5 8-1.7z" />
      </>
    ),
    user: (
      <>
        <circle cx="12" cy="7.5" r="3.5" />
        <path d="M5 20c.3-4.2 2.6-6.4 7-6.4s6.7 2.2 7 6.4z" />
      </>
    ),
    admin: (
      <>
        <path d="M12 3 20 6v5c0 5-3.2 8.2-8 10-4.8-1.8-8-5-8-10V6z" />
        <circle cx="12" cy="10" r="2.1" />
        <path d="M8.7 16c.4-2 1.5-3 3.3-3s2.9 1 3.3 3" />
      </>
    ),
    message: (
      <>
        <path d="M5 18.5 3.8 21l3.7-1.2A9 9 0 1 0 3 12c0 2.6.8 4.7 2 6.5Z" />
        <path d="M8 12h.01M12 12h.01M16 12h.01" />
      </>
    ),
    help: (
      <>
        <circle cx="12" cy="12" r="9" />
        <path d="M9.8 9a2.4 2.4 0 1 1 3.6 2.1c-.9.5-1.4 1.1-1.4 2.2" />
        <path d="M12 17h.01" />
      </>
    ),
    search: (
      <>
        <circle cx="10.8" cy="10.8" r="6.8" />
        <path d="m16 16 4.5 4.5" />
      </>
    ),
    menu: <path d="M4 7h16M4 12h16M4 17h16" />,
    arrow: <path d="M5 12h14m-5-5 5 5-5 5" />,
    racket: (
      <>
        <ellipse
          cx="13.7"
          cy="8.5"
          rx="5.2"
          ry="6.6"
          transform="rotate(42 13.7 8.5)"
        />
        <path d="m9.5 13.7-6 6M6.4 16.7l2 2M11.2 4.8l6.4 6.4M8.8 7.4l6.3 6.3M8.3 11l6.8-6.8" />
        <path d="m3.5 19.7.9.9" />
      </>
    ),
    clipboard: (
      <>
        <path d="M8 5H5.5v16h13V5H16" />
        <rect x="8" y="3" width="8" height="4" rx="1.5" />
        <path d="m8 12 1.5 1.5L12 10.8M8 17l1.5 1.5 2.5-2.7M14 12h2M14 17h2" />
      </>
    ),
    card: (
      <>
        <rect x="3" y="5" width="18" height="14" rx="2" />
        <path d="M3 9h18M7 14h4" />
      </>
    ),
    spark: (
      <>
        <path d="m12 3 1.3 4.3L17 9l-3.7 1.7L12 15l-1.3-4.3L7 9l3.7-1.7z" />
        <path d="m18.5 15 .7 2.2 1.8.8-1.8.8-.7 2.2-.7-2.2L16 18l1.8-.8z" />
      </>
    ),
    history: (
      <>
        <path d="M4.5 9A8 8 0 1 1 4 14" />
        <path d="M4 4v5h5M12 7v5l3 2" />
      </>
    ),
    settings: (
      <>
        <circle cx="12" cy="12" r="3" />
        <path d="M19 12a7 7 0 0 0-.1-1l2-1.5-2-3.4-2.3 1a8 8 0 0 0-1.8-1L14.5 3h-4L10 6.1a8 8 0 0 0-1.8 1l-2.3-1-2 3.4 2 1.5a7 7 0 0 0 0 2l-2 1.5 2 3.4 2.3-1a8 8 0 0 0 1.8 1l.5 3.1h4l.4-3.1a8 8 0 0 0 1.8-1l2.3 1 2-3.4-2-1.5a7 7 0 0 0 0-1Z" />
      </>
    ),
    bell: (
      <>
        <path d="M6 17h12l-1.5-2.5V10a4.5 4.5 0 0 0-9 0v4.5z" />
        <path d="M10 20h4" />
      </>
    ),
    link: (
      <>
        <path d="m9.5 14.5 5-5" />
        <path d="M7.4 16.6 5.8 18.2a3 3 0 1 1-4.2-4.2l3-3a3 3 0 0 1 4.2 0M16.6 7.4l1.6-1.6a3 3 0 1 1 4.2 4.2l-3 3a3 3 0 0 1-4.2 0" />
      </>
    ),
  };

  return (
    <svg
      aria-hidden="true"
      height={size}
      viewBox="0 0 24 24"
      width={size}
      {...common}
    >
      {paths[name]}
    </svg>
  );
}

export type { IconName };

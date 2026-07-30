"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Icon } from "./icon";
import { navItems, searchItems } from "../manual-data";

type ManualShellProps = {
  children: React.ReactNode;
};

export function ManualShell({ children }: ManualShellProps) {
  const pathname = usePathname();
  const [menuOpen, setMenuOpen] = useState(false);
  const [searchOpen, setSearchOpen] = useState(false);
  const [query, setQuery] = useState("");
  const searchInputRef = useRef<HTMLInputElement>(null);

  const closeSearch = useCallback(() => {
    setSearchOpen(false);
    setQuery("");
  }, []);

  const results = useMemo(() => {
    const normalized = query.trim().toLowerCase();
    if (!normalized) return searchItems;
    return searchItems.filter((item) =>
      `${item.title} ${item.description} ${item.keywords}`
        .toLowerCase()
        .includes(normalized),
    );
  }, [query]);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setSearchOpen(true);
      }
      if (event.key === "Escape") {
        closeSearch();
        setMenuOpen(false);
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [closeSearch]);

  useEffect(() => {
    if (searchOpen) {
      window.requestAnimationFrame(() => searchInputRef.current?.focus());
    }
  }, [searchOpen]);

  return (
    <div>
      <header className="site-header">
        <button
          aria-expanded={menuOpen}
          aria-label="メニューを開く"
          className="mobile-menu-button"
          onClick={() => setMenuOpen((open) => !open)}
          type="button"
        >
          <Icon name="menu" />
        </button>

        <Link className="brand" href="/" onClick={() => setMenuOpen(false)}>
          <span className="brand-long">
            Shuttlers Match App 操作マニュアル
          </span>
          <span className="brand-short">操作マニュアル</span>
        </Link>

        <button
          className="search-trigger"
          onClick={() => setSearchOpen(true)}
          type="button"
        >
          <Icon name="search" size={25} />
          <span>マニュアルを検索</span>
          <kbd>⌘ K</kbd>
        </button>

        <span className="version-badge">v1.6.1</span>
      </header>

      <aside className={`sidebar ${menuOpen ? "open" : ""}`}>
        <nav aria-label="マニュアル">
          <ul className="nav-list">
            {navItems.map((item) => {
              const isActive =
                item.href === "/"
                  ? pathname === "/"
                  : pathname.startsWith(item.href);
              return (
                <li key={item.href}>
                  <Link
                    aria-current={isActive ? "page" : undefined}
                    className={`nav-link ${isActive ? "active" : ""}`}
                    href={item.href}
                    onClick={() => setMenuOpen(false)}
                  >
                    <span className="nav-icon">
                      <Icon name={item.icon} size={27} />
                    </span>
                    <span>{item.label}</span>
                  </Link>
                </li>
              );
            })}
          </ul>
        </nav>
      </aside>

      {menuOpen && (
        <button
          aria-label="メニューを閉じる"
          className="sidebar-backdrop"
          onClick={() => setMenuOpen(false)}
          type="button"
        />
      )}

      <main className="manual-main">
        <div className="content-container">{children}</div>
      </main>

      {searchOpen && (
        <div
          aria-label="マニュアル検索"
          aria-modal="true"
          className="search-overlay"
          onMouseDown={(event) => {
            if (event.currentTarget === event.target) closeSearch();
          }}
          role="dialog"
        >
          <div className="search-dialog">
            <div className="search-input-wrap">
              <Icon name="search" size={25} />
              <input
                aria-label="検索キーワード"
                className="search-input"
                onChange={(event) => setQuery(event.target.value)}
                placeholder="操作や機能を検索"
                ref={searchInputRef}
                type="search"
                value={query}
              />
              <button
                className="search-close"
                onClick={closeSearch}
                type="button"
              >
                ESC
              </button>
            </div>
            <div className="search-results">
              {results.length > 0 ? (
                results.map((item) => (
                  <Link
                    className="search-result"
                    href={item.href}
                    key={item.href}
                    onClick={closeSearch}
                  >
                    <strong>{item.title}</strong>
                    <span>{item.description}</span>
                  </Link>
                ))
              ) : (
                <p className="search-empty">
                  「{query}」に一致する項目はありません
                </p>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

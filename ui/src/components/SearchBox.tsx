import { useState } from "react";
import { useSearch } from "../api/hooks";
import type { SearchHit } from "../api/types";

interface SearchBoxProps {
  onPick: (hit: SearchHit) => void;
}

// Debounced lookup. The hook itself debounces refetches via React
// Query's dedup window, but we throttle the keystrokes a touch here
// to avoid spawning a request on every letter.
export default function SearchBox({ onPick }: SearchBoxProps) {
  const [text, setText] = useState("");
  const [debounced, setDebounced] = useState("");
  const { data, isFetching } = useSearch(debounced);

  function onChange(value: string) {
    setText(value);
    window.clearTimeout((onChange as unknown as { _t?: number })._t);
    (onChange as unknown as { _t?: number })._t = window.setTimeout(() => {
      setDebounced(value);
    }, 150);
  }

  return (
    <div className="relative">
      <input
        type="text"
        value={text}
        onChange={(e) => onChange(e.target.value)}
        placeholder="Search nodes by name or id…"
        className="w-full bg-slate-800 border border-slate-700 rounded-md px-3 py-2 text-sm placeholder:text-slate-500 focus:outline-none focus:border-accent"
      />
      {debounced && data && data.length > 0 && (
        <ul className="absolute z-10 mt-1 w-full max-h-64 overflow-auto bg-slate-900 border border-slate-700 rounded-md shadow-lg">
          {data.map((hit) => (
            <li
              key={hit.id}
              className="px-3 py-2 text-sm hover:bg-slate-800 cursor-pointer flex justify-between items-center gap-2"
              onClick={() => {
                onPick(hit);
                setText("");
                setDebounced("");
              }}
            >
              <div className="min-w-0">
                <div className="text-slate-100 truncate">{hit.name || hit.id}</div>
                <div className="text-xs text-slate-500 truncate">{hit.id}</div>
              </div>
              <span className="pill">{hit.kind}</span>
            </li>
          ))}
        </ul>
      )}
      {debounced && !isFetching && data && data.length === 0 && (
        <div className="absolute z-10 mt-1 w-full bg-slate-900 border border-slate-700 rounded-md px-3 py-2 text-sm text-slate-400">
          no matches
        </div>
      )}
    </div>
  );
}

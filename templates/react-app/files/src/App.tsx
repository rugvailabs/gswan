import { useCounter } from "./store";

export function App() {
  const { count, increment, reset } = useCounter();
  return (
    <main className="mx-auto flex min-h-screen max-w-md flex-col items-center justify-center gap-4 p-4">
      <h1 className="text-2xl font-semibold">Hello</h1>
      <p aria-label="count" className="text-4xl tabular-nums">
        {count}
      </p>
      <div className="flex gap-2">
        <button className="rounded bg-blue-600 px-4 py-2 text-white" onClick={increment}>
          Add one
        </button>
        <button className="rounded border px-4 py-2" onClick={reset}>
          Reset
        </button>
      </div>
    </main>
  );
}

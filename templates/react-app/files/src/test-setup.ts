import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";
import { useCounter } from "./store";

afterEach(() => {
  cleanup();
  useCounter.setState({ count: 0 });
});

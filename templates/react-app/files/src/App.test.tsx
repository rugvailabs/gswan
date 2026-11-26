import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { App } from "./App";

test("add one and reset", async () => {
  const user = userEvent.setup();
  render(<App />);
  expect(screen.getByLabelText("count")).toHaveTextContent("0");
  await user.click(screen.getByRole("button", { name: "Add one" }));
  await user.click(screen.getByRole("button", { name: "Add one" }));
  expect(screen.getByLabelText("count")).toHaveTextContent("2");
  await user.click(screen.getByRole("button", { name: "Reset" }));
  expect(screen.getByLabelText("count")).toHaveTextContent("0");
});

import { Journey } from "./Journey";
import { NeedsYou } from "./NeedsYou";
import { Parts } from "./Parts";
import { Story } from "./Story";
import { Summary } from "./Summary";
import { YourProject } from "./YourProject";

/** The plain-English dashboard: what is being built, what needs you, where it stands, and where the result is. */
export function Overview() {
  return (
    <div className="flex flex-col gap-4">
      <Summary />
      <NeedsYou />
      <Journey />
      <div className="grid grid-cols-[minmax(0,1fr)] gap-4 lg:grid-cols-[minmax(0,1fr)_24rem]">
        <div className="flex min-w-0 flex-col gap-4">
          <YourProject />
          <Parts />
        </div>
        <Story />
      </div>
    </div>
  );
}

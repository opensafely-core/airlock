import type { Meta, StoryObj } from "@storybook/web-components-vite";

import { html } from "lit";
import "../assets/src/scripts/components/dropdown";
import "../assets/src/scripts/components/dropdown-item";

type DropdownArgs = {
  title: string;
};
const meta = {
  title: "UI/Dropdown",
  tags: ["autodocs"],
  render: ({ title }) => html`
    <div style="display:flex;justify-content:center">
      <os-dropdown .title=${title}>
        <os-dropdown-item
          icon="/static/icons/cloud_download.svg"
          href="/download"
          link-id="download-button"
        >
          Download
        </os-dropdown-item>
      </os-dropdown>
    </div>
  `,
  argTypes: {},
} satisfies Meta<DropdownArgs>;

export default meta;
type Story = StoryObj<DropdownArgs>;

export const Primary: Story = {
  args: {
    title: "More",
  },
};

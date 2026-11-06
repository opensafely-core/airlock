import { LitElement, css, html } from "lit";
import { customElement, property } from "lit/decorators.js";

@customElement("airlock-dropdown")
export class Dropdown extends LitElement {
  @property({ type: String })
  title = "More...";

  private handleDocumentPointerDown = (event: PointerEvent) => {
    if (event.composedPath().includes(this)) return;
    this.closeDropdown();
  };

  private handleDetailsKeyDown = (event: KeyboardEvent) => {
    if (event.key !== "Escape") return;

    const details = event.currentTarget as HTMLDetailsElement;
    if (!details.open) return;

    event.preventDefault();
    details.open = false;
    this.renderRoot.querySelector("summary")?.focus();
  };

  private closeDropdown() {
    const details =
      this.renderRoot.querySelector<HTMLDetailsElement>("details");
    if (details) details.open = false;
  }

  connectedCallback() {
    super.connectedCallback();
    document.addEventListener("pointerdown", this.handleDocumentPointerDown);
  }

  disconnectedCallback() {
    document.removeEventListener("pointerdown", this.handleDocumentPointerDown);
    super.disconnectedCallback();
  }

  static styles = css`
    .more-dropdown {
      display: flex;
      position: relative;
    }

    .more-dropdown__btn {
      align-items: center;
      background: white;
      border: 1px solid var(--color-gray-300);
      border-radius: 0.375rem;
      color: var(--color-gray-900);
      cursor: pointer;
      display: inline-flex;
      font-size: 0.875rem;
      font-weight: 600;
      line-height: 1.25rem;
      padding: 0.5rem 0.75rem;
      position: relative;
      transition:
        background-color 0.2s,
        color 0.2s,
        border-color 0.2s;
    }

    .more-dropdown__btn:focus,
    .more-dropdown__btn:active {
      outline: 2px solid var(--color-oxford-600);
      outline-offset: 2px;
    }

    .more-dropdown__nav {
      background: white;
      border-radius: 0.375rem;
      box-shadow:
        rgb(255, 255, 255) 0px 0px 0px 0px,
        rgba(0, 0, 0, 0.05) 0px 0px 0px 1px,
        rgba(0, 0, 0, 0.1) 0px 10px 15px -3px,
        rgba(0, 0, 0, 0.1) 0px 4px 6px -4px;
      display: flex;
      flex-direction: column;
      margin-top: 0.5rem;
      padding: 0.25 0;
      position: absolute;
      right: 0;
      transform-origin: top right;
      width: 14rem;
      top: 100%;
      list-style: none;
      margin: 0;
      padding: 0;
      margin-top: 0.5rem;
    }
    .more-dropdown[open] .open {
      display: block;
    }
    .more-dropdown[open] .closed {
      display: none;
    }
    .open {
      display: none;
    }
    .icon-container {
      opacity: 0.75;
      margin-left: 0.25rem;
      display: flex;
      align-items: center;
    }
  `;

  render() {
    return html`
      <details class="more-dropdown" @keydown=${this.handleDetailsKeyDown}>
        <summary class="more-dropdown__btn" id="file-button-more">
          ${this.title}
          <span class="icon-container">
            <img
              height="16"
              width="16"
              class="icon closed"
              src="/static/icons/keyboard_arrow_down.svg"
              alt=""
            />
            <img
              height="16"
              width="16"
              class="icon open"
              src="/static/icons/keyboard_arrow_up.svg"
              alt=""
            />
          </span>
        </summary>
        <ul class="more-dropdown__nav">
          <slot></slot>
        </ul>
      </details>
    `;
  }
}

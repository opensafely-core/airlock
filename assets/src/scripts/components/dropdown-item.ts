import { LitElement, css, html } from "lit";
import { customElement, property } from "lit/decorators.js";

@customElement("os-dropdown-item")
export class Dropdown extends LitElement {
  @property({ type: String })
  icon = "";

  @property({ type: String })
  href = "";

  @property({ type: String })
  target = "";

  @property({ type: String })
  rel = "";

  @property({ type: String, attribute: "link-id" })
  linkId = "";

  static styles = css`
    li {
      list-style: none;
      margin: 0;
      padding: 0;
    }
    .dropdown__link {
      align-items: center;
      color: var(--color-gray-700);
      display: flex;
      font-size: 0.875rem;
      line-height: 1.25rem;
      padding: 0.5rem 1rem;
      gap: 0.75rem;
      text-decoration: none;
    }

    .dropdown__link:hover {
      background-color: var(--color-gray-100);
      border-color: var(--color-gray-400);
      color: var(--color-gray-700);
    }

    .dropdown__icon {
      filter: invert(76%) sepia(12%) saturate(264%) hue-rotate(179deg)
        brightness(83%) contrast(93%);
      height: 1.25rem;
      width: 1.25rem;
    }
  `;
  render() {
    return html`
      <li class="dropdown__item">
        <a
          href="${this.href}"
          class="dropdown__link"
          target="${this.target}"
          rel="${this.rel}"
          id="${this.linkId}"
        >
          <img class="dropdown__icon" src="${this.icon}" alt="" />
          <slot></slot>
        </a>
      </li>
    `;
  }
}

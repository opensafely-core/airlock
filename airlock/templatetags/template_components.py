from django import template
from django.utils.html import conditional_escape, format_html
from django.utils.safestring import mark_safe


register = template.Library()


class AttrsNode(template.Node):
    def __init__(self, items):
        # items: list of (html_attr_name, filter_expr_or_None, context_key)
        self.items = items

    def render(self, context):
        # Look up positional args only in the topmost context dict — that's the
        # {% include %}'s pushed extra_context. Without this restriction, an
        # outer-scope variable like `form` (a Django Form) leaks into a child
        # component's attribute lookup.
        top = context.dicts[-1] if context.dicts else {}
        parts = []
        for html_name, expr, ctx_key in self.items:
            if expr is not None:
                value = expr.resolve(context)
            else:
                value = top.get(ctx_key)

            if value is None or value is False or value == "":
                continue

            if value is True:
                parts.append(conditional_escape(html_name))
            else:
                parts.append(format_html('{}="{}"', html_name, value))

        return " ".join(parts)


@register.tag("attrs")
def attrs_tag(parser, token):
    """
    Render HTML attributes from template context variables.

    Usage: {% attrs name1 name2 name3=expr %}

    - Positional args: look up the name in the topmost context dict (the
      include's pushed extra_context) only — skip if falsy or absent.
    - Keyword args: evaluate expr in full context (supports filters like
      |default:).
    - Underscores in names are converted to hyphens in HTML output
      (data_modal → data-modal, hx_post → hx-post)
    - Boolean True renders as a bare attribute (disabled, required, multiple)
    - False/None/empty string are skipped
    """
    bits = token.split_contents()
    items = []
    for bit in bits[1:]:
        if "=" in bit:
            name, _, expr_str = bit.partition("=")
            html_name = name.replace("_", "-")
            expr = parser.compile_filter(expr_str)
            items.append((html_name, expr, name))
        else:
            html_name = bit.replace("_", "-")
            items.append((html_name, None, bit))
    return AttrsNode(items)


class SetVarNode(template.Node):
    def __init__(self, assignments):
        self.assignments = assignments

    def render(self, context):
        for name, expr in self.assignments:
            context[name] = expr.resolve(context)
        return ""


@register.tag("setvar")
def setvar_tag(parser, token):
    """
    Set template variables without a closing tag.

    Usage: {% setvar name=value %} or {% setvar name=var|filter %}

    Replaces Slippers' {% var %} tag. Sets variables in the current context
    layer, accessible for the remainder of the template.
    """
    bits = token.split_contents()
    if len(bits) < 2:
        raise template.TemplateSyntaxError(
            f"{bits[0]} requires at least one name=value argument"
        )
    assignments = []
    for bit in bits[1:]:
        if "=" not in bit:
            raise template.TemplateSyntaxError(
                f"{bits[0]}: expected name=value, got {bit!r}"
            )
        name, _, expr_str = bit.partition("=")
        assignments.append((name, parser.compile_filter(expr_str)))
    return SetVarNode(assignments)


class FragmentNode(template.Node):
    def __init__(self, nodelist, var_name):
        self.nodelist = nodelist
        self.var_name = var_name

    def render(self, context):
        context[self.var_name] = mark_safe(self.nodelist.render(context))
        return ""


@register.tag("fragment")
def fragment_tag(parser, token):
    """
    Capture rendered template content into a context variable.

    Usage: {% fragment as varname %}...{% endfragment %}

    Replaces Slippers' {% fragment %} tag. The rendered content is stored
    as a safe HTML string in the context, available for the rest of the
    template as {{ varname }}.
    """
    bits = token.split_contents()
    if len(bits) != 3 or bits[1] != "as":
        raise template.TemplateSyntaxError(
            f"{bits[0]} expects 'as varname', got: {' '.join(bits[1:])!r}"
        )
    var_name = bits[2]
    nodelist = parser.parse(("endfragment",))
    parser.delete_first_token()
    return FragmentNode(nodelist, var_name)


_BODY_KEY = "__component_body__"


class ComponentNode(template.Node):
    def __init__(self, template_name, nodelist, extra_context):
        self.template_name = template_name
        self.nodelist = nodelist
        self.extra_context = extra_context

    def render(self, context):
        body = mark_safe(self.nodelist.render(context))
        values = {
            name: expr.resolve(context) for name, expr in self.extra_context.items()
        }
        # Stash body under a sentinel key. {% body %} reads it from the topmost
        # context dict only, which prevents the outer component's body from
        # leaking into nested {% include %}s that don't push their own body.
        values[_BODY_KEY] = body
        tmpl_name = self.template_name.resolve(context)
        tmpl = context.template.engine.get_template(tmpl_name)
        with context.push(**values):
            return tmpl.render(context)


@register.tag("component")
def component_tag(parser, token):
    """
    Render a component template, capturing the block's content as the body slot.

    Usage: {% component "path.html" with key=value ... %}...{% endcomponent %}

    Inside the component template, use {% body %} to render the slot content.
    """
    bits = token.split_contents()
    if len(bits) < 2:
        raise template.TemplateSyntaxError(f"{bits[0]} requires a template path")
    template_name = parser.compile_filter(bits[1])

    extra_context = {}
    i = 2
    if i < len(bits):
        if bits[i] != "with":
            raise template.TemplateSyntaxError(
                f"{bits[0]} expects 'with' before extra arguments, got {bits[i]!r}"
            )
        i += 1
    while i < len(bits):
        bit = bits[i]
        if "=" not in bit:
            raise template.TemplateSyntaxError(
                f"{bits[0]} 'with' args must be name=value, got {bit!r}"
            )
        name, _, expr_str = bit.partition("=")
        extra_context[name] = parser.compile_filter(expr_str)
        i += 1

    nodelist = parser.parse(("endcomponent",))
    parser.delete_first_token()
    return ComponentNode(template_name, nodelist, extra_context)


@register.simple_tag(takes_context=True)
def body(context):
    """
    Render the body slot of the surrounding {% component %} invocation.

    Reads only from the topmost context dict (the component's pushed values),
    so an outer component's body never leaks into nested {% include %}s.
    """
    top = context.dicts[-1] if context.dicts else {}
    return top.get(_BODY_KEY, "")

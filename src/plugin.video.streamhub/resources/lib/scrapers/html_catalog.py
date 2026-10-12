"""Small HTML helpers for public catalog pages; no JavaScript execution."""
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

_VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input",
         "link", "meta", "param", "source", "track", "wbr"}


class Node:
    def __init__(self, tag="", attrs=None):
        self.tag = tag
        self.attrs = dict(attrs or ())
        self.children = []

    def walk(self):
        yield self
        for child in self.children:
            if isinstance(child, Node):
                yield from child.walk()

    def has_class(self, name):
        return name in (self.attrs.get("class") or "").split()

    def find_class(self, name):
        return next((node for node in self.walk() if node.has_class(name)), None)

    def find_tag(self, tag):
        return next((node for node in self.walk() if node.tag == tag), None)

    def text(self):
        if self.tag in ("script", "style"):
            return ""
        return " ".join(" ".join(child.text() if isinstance(child, Node) else child
                                 for child in self.children).split())


class _TreeParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Node()
        self.stack = [self.root]

    def handle_starttag(self, tag, attrs):
        node = Node(tag, attrs)
        self.stack[-1].children.append(node)
        if tag not in _VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in _VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                return

    def handle_data(self, data):
        self.stack[-1].children.append(data)


def parse_html(html):
    parser = _TreeParser()
    parser.feed(html or "")
    parser.close()
    return parser.root


def http_url(base, value, same_host=False):
    if not value or not isinstance(value, str):
        return ""
    try:
        candidate = urljoin(base, value.strip())
        parsed = urlparse(candidate)
    except ValueError:
        return ""
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return ""
    if parsed.username or parsed.password:
        return ""
    if same_host and parsed.hostname != urlparse(base).hostname:
        return ""
    return candidate

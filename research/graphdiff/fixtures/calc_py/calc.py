"""A small recursive-descent calculator — Python half of a cross-language twin.

Faithfully mirrored in ../calc_js/calc.js. Both implement the same call graph:
tokenize -> Parser.parse -> _expr -> _term -> _factor (recursive), plus evaluate()
over the produced tuples and a main() driver. Used to measure how well the graph-diff
oracle's leaf mode recognises a real translation across languages.
"""


def is_space(ch):
    return ch == " " or ch == "\t"


def is_digit(ch):
    return "0" <= ch <= "9"


def tokenize(src):
    tokens = []
    i = 0
    while i < len(src):
        ch = src[i]
        if is_space(ch):
            i += 1
            continue
        if is_digit(ch):
            j = i
            while j < len(src) and is_digit(src[j]):
                j += 1
            tokens.append(("num", int(src[i:j])))
            i = j
            continue
        tokens.append(("op", ch))
        i += 1
    return tokens


class Parser:
    def __init__(self, tokens):
        self.tokens = tokens
        self.pos = 0

    def peek(self):
        if self.pos < len(self.tokens):
            return self.tokens[self.pos]
        return ("eof", None)

    def advance(self):
        tok = self.peek()
        self.pos += 1
        return tok

    def parse(self):
        node = self._expr()
        return node

    def _expr(self):
        node = self._term()
        while self.peek()[1] in ("+", "-"):
            op = self.advance()[1]
            right = self._term()
            node = ("bin", op, node, right)
        return node

    def _term(self):
        node = self._factor()
        while self.peek()[1] in ("*", "/"):
            op = self.advance()[1]
            right = self._factor()
            node = ("bin", op, node, right)
        return node

    def _factor(self):
        tok = self.advance()
        if tok[0] == "num":
            return ("lit", tok[1])
        if tok[1] == "(":
            node = self._expr()
            self.advance()  # consume ')'
            return node
        return ("lit", 0)


def evaluate(node):
    if node[0] == "lit":
        return node[1]
    op, left, right = node[1], evaluate(node[2]), evaluate(node[3])
    if op == "+":
        return left + right
    if op == "-":
        return left - right
    if op == "*":
        return left * right
    return left / right


def calc(src):
    tokens = tokenize(src)
    parser = Parser(tokens)
    tree = parser.parse()
    return evaluate(tree)


def main():
    print(calc("1 + 2 * 3"))
    print(calc("(1 + 2) * 3"))


if __name__ == "__main__":
    main()

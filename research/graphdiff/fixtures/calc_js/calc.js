// A small recursive-descent calculator — JS half of a cross-language twin.
//
// Faithfully mirrored from ../calc_py/calc.py. Same call graph:
// tokenize -> Parser.parse -> _expr -> _term -> _factor (recursive), plus evaluate()
// over the produced arrays and a main() driver. Used to measure how well the
// graph-diff oracle's leaf mode recognises a real translation across languages.

function is_space(ch) {
  return ch === " " || ch === "\t";
}

function is_digit(ch) {
  return ch >= "0" && ch <= "9";
}

function tokenize(src) {
  const tokens = [];
  let i = 0;
  while (i < src.length) {
    const ch = src[i];
    if (is_space(ch)) {
      i += 1;
      continue;
    }
    if (is_digit(ch)) {
      let j = i;
      while (j < src.length && is_digit(src[j])) {
        j += 1;
      }
      tokens.push(["num", parseInt(src.slice(i, j), 10)]);
      i = j;
      continue;
    }
    tokens.push(["op", ch]);
    i += 1;
  }
  return tokens;
}

class Parser {
  constructor(tokens) {
    this.tokens = tokens;
    this.pos = 0;
  }

  peek() {
    if (this.pos < this.tokens.length) {
      return this.tokens[this.pos];
    }
    return ["eof", null];
  }

  advance() {
    const tok = this.peek();
    this.pos += 1;
    return tok;
  }

  parse() {
    const node = this._expr();
    return node;
  }

  _expr() {
    let node = this._term();
    while (this.peek()[1] === "+" || this.peek()[1] === "-") {
      const op = this.advance()[1];
      const right = this._term();
      node = ["bin", op, node, right];
    }
    return node;
  }

  _term() {
    let node = this._factor();
    while (this.peek()[1] === "*" || this.peek()[1] === "/") {
      const op = this.advance()[1];
      const right = this._factor();
      node = ["bin", op, node, right];
    }
    return node;
  }

  _factor() {
    const tok = this.advance();
    if (tok[0] === "num") {
      return ["lit", tok[1]];
    }
    if (tok[1] === "(") {
      const node = this._expr();
      this.advance(); // consume ')'
      return node;
    }
    return ["lit", 0];
  }
}

function evaluate(node) {
  if (node[0] === "lit") {
    return node[1];
  }
  const op = node[1];
  const left = evaluate(node[2]);
  const right = evaluate(node[3]);
  if (op === "+") {
    return left + right;
  }
  if (op === "-") {
    return left - right;
  }
  if (op === "*") {
    return left * right;
  }
  return left / right;
}

function calc(src) {
  const tokens = tokenize(src);
  const parser = new Parser(tokens);
  const tree = parser.parse();
  return evaluate(tree);
}

function main() {
  console.log(calc("1 + 2 * 3"));
  console.log(calc("(1 + 2) * 3"));
}

main();

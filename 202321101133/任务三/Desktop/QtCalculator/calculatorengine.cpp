#include "calculatorengine.h"
#include <cmath>
#include <cctype>
#include <QSet>
#include <QMap>

// 已注册函数
static const QSet<QString> FUNC_NAMES = {"pow", "sin", "cos", "tan", "log", "ln", "sqrt", "abs"};
// 已注册常量 → 值
static const QMap<QString, double> CONST_VALUES = {{"pi", M_PI}, {"e", M_E}};

// ════════════════════ Lexer ════════════════════

Lexer::Lexer(const QString& text)
    : m_text(text), m_length(text.length()) {}

QChar Lexer::peek() const {
    if (m_pos < m_length)
        return m_text[m_pos];
    return QChar();
}

QChar Lexer::advance() {
    QChar ch = peek();
    m_pos++;
    return ch;
}

void Lexer::skipWhitespace() {
    while (m_pos < m_length && m_text[m_pos].isSpace())
        m_pos++;
}

double Lexer::scanNumber() {
    int start = m_pos;
    while (m_pos < m_length && (m_text[m_pos].isDigit() || m_text[m_pos] == '.'))
        m_pos++;
    return m_text.mid(start, m_pos - start).toDouble();
}

QString Lexer::scanIdentifier() {
    int start = m_pos;
    while (m_pos < m_length && m_text[m_pos].isLetter())
        m_pos++;
    return m_text.mid(start, m_pos - start).toLower();
}

QVector<Token> Lexer::tokenize() {
    QVector<Token> tokens;

    while (m_pos < m_length) {
        skipWhitespace();
        if (m_pos >= m_length)
            break;

        QChar ch = m_text[m_pos];

        // 数字
        if (ch.isDigit() || ch == '.') {
            tokens.append(Token(TokType::Number, scanNumber()));
        }
        // 标识符（函数名 / 常量名）
        else if (ch.isLetter()) {
            QString name = scanIdentifier();
            if (FUNC_NAMES.contains(name))
                tokens.append(Token(TokType::Func, 0, name));
            else if (CONST_VALUES.contains(name))
                tokens.append(Token(TokType::Const, CONST_VALUES[name], ""));
            else
                throw std::runtime_error(
                    QString("未识别的名称: '%1'").arg(name).toStdString());
        }
        // 运算符
        else if (ch == '+') { m_pos++; tokens.append(Token(TokType::Plus)); }
        else if (ch == '-') { m_pos++; tokens.append(Token(TokType::Minus)); }
        else if (ch == '*') { m_pos++; tokens.append(Token(TokType::Mul)); }
        else if (ch == '/') { m_pos++; tokens.append(Token(TokType::Div)); }
        else if (ch == '^') { m_pos++; tokens.append(Token(TokType::Pow)); }
        else if (ch == '(') { m_pos++; tokens.append(Token(TokType::LParen)); }
        else if (ch == ')') { m_pos++; tokens.append(Token(TokType::RParen)); }
        else if (ch == ',') { m_pos++; tokens.append(Token(TokType::Comma)); }
        else
            throw std::runtime_error(
                QString("无法识别的字符: '%1'").arg(ch).toStdString());
    }

    tokens.append(Token(TokType::Eof));
    return tokens;
}

// ════════════════════ Parser ════════════════════

Parser::Parser(const QVector<Token>& tokens) : m_tokens(tokens) {}

const Token& Parser::peek() const {
    return m_tokens[m_pos];
}

Token Parser::advance() {
    return m_tokens[m_pos++];
}

Token Parser::expect(TokType type) {
    if (m_tokens[m_pos].type != type)
        throw std::runtime_error("语法错误：括号不匹配");
    return m_tokens[m_pos++];
}

double Parser::parse() {
    double result = expression();
    if (peek().type != TokType::Eof)
        throw std::runtime_error("语法错误：表达式末尾有多余内容");
    return result;
}

// expression → term (('+'|'-') term)*
double Parser::expression() {
    double left = term();
    while (peek().type == TokType::Plus || peek().type == TokType::Minus) {
        Token op = advance();
        double right = term();
        if (op.type == TokType::Plus)
            left += right;
        else
            left -= right;
    }
    return left;
}

// term → unary (('*'|'/') unary)*
double Parser::term() {
    double left = unary();
    while (peek().type == TokType::Mul || peek().type == TokType::Div) {
        Token op = advance();
        double right = unary();
        if (op.type == TokType::Mul)
            left *= right;
        else {
            if (right == 0.0)
                throw std::runtime_error("数学错误：除以零");
            left /= right;
        }
    }
    return left;
}

// unary → ('+'|'-') unary | power
double Parser::unary() {
    if (peek().type == TokType::Minus) {
        advance();
        return -unary();
    }
    if (peek().type == TokType::Plus) {
        advance();
        return unary();
    }
    return power();
}

// power → primary ('^' power)?   右结合
double Parser::power() {
    double left = primary();
    if (peek().type == TokType::Pow) {
        advance();
        double right = power();   // 递归右侧 → 右结合
        return checkedPow(left, right);
    }
    return left;
}

// primary → NUMBER | CONST | '(' expr ')' | FUNC '(' args ')'
double Parser::primary() {
    const Token& tok = peek();

    if (tok.type == TokType::Number) {
        advance();
        return tok.value;
    }

    if (tok.type == TokType::Const) {
        advance();
        return tok.value;
    }

    if (tok.type == TokType::LParen) {
        advance();
        double result = expression();
        expect(TokType::RParen);
        return result;
    }

    if (tok.type == TokType::Func)
        return parseFuncCall();

    throw std::runtime_error("语法错误：意外的 token");
}

// 函数调用
double Parser::parseFuncCall() {
    QString funcName = advance().funcName;
    expect(TokType::LParen);

    // 单参数函数
    static const QSet<QString> singleArg = {"sin", "cos", "tan", "log", "ln", "sqrt", "abs"};
    if (singleArg.contains(funcName)) {
        double arg = expression();
        expect(TokType::RParen);
        return dispatchSingle(funcName, arg);
    }

    // pow: 双参数（允许留空）
    QVector<double> args;

    // 第一个参数
    if (peek().type == TokType::RParen) {
        // pow() 无参数
    } else if (peek().type == TokType::Comma) {
        args.append(1.0);   // pow(, → 默认 1
    } else {
        args.append(expression());
    }

    // 第二个参数
    if (peek().type == TokType::Comma) {
        advance();
        if (peek().type == TokType::RParen)
            args.append(1.0);   // pow(x, ) → 默认 1
        else
            args.append(expression());
    }

    expect(TokType::RParen);

    if (funcName == "pow") {
        if (args.isEmpty())
            throw std::runtime_error("pow() 需要至少 1 个参数");
        if (args.size() == 1)
            args.append(1.0);
        if (args.size() != 2)
            throw std::runtime_error("pow() 最多接受 2 个参数");
        return checkedPow(args[0], args[1]);
    }

    throw std::runtime_error("未实现的函数");
}

// 单参数函数分派
double Parser::dispatchSingle(const QString& name, double arg) {
    if (name == "sin")  return std::sin(arg);
    if (name == "cos")  return std::cos(arg);
    if (name == "tan")  return std::tan(arg);
    if (name == "log")  return std::log10(arg);
    if (name == "ln")   return std::log(arg);
    if (name == "sqrt") {
        if (arg < 0)
            throw std::runtime_error(
                QString("实数域错误：sqrt(%1) 在实数范围内无定义").arg(arg).toStdString());
        return std::sqrt(arg);
    }
    if (name == "abs")  return std::abs(arg);
    throw std::runtime_error("未实现的函数");
}

// 乘方/开方，实数域校验
double Parser::checkedPow(double base, double exp) {
    // 负底数 + 非整数指数 → 实数域无定义
    if (base < 0 && exp != std::floor(exp))
        throw std::runtime_error(
            QString("实数域错误：负底数 (%1) 的 %2 次方在实数范围内无定义")
                .arg(base).arg(exp).toStdString());
    return std::pow(base, exp);
}

// ════════════════════ CalculatorEngine ════════════════════

bool CalculatorEngine::evaluate(const QString& expr, double& result, QString& error) {
    try {
        Lexer lexer(expr);
        QVector<Token> tokens = lexer.tokenize();
        Parser parser(tokens);
        result = parser.parse();
        return true;
    } catch (const std::exception& e) {
        error = QString::fromStdString(e.what());
        return false;
    }
}

QString CalculatorEngine::formatResult(double value) {
    if (value == 0.0)
        return "0";
    if (value == std::floor(value) && std::abs(value) < 1e15)
        return QString::number(static_cast<qint64>(value));
    return QString::number(value, 'g', 15);
}

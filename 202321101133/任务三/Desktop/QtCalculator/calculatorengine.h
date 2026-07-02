#ifndef CALCULATORENGINE_H
#define CALCULATORENGINE_H

#include <QString>
#include <QVector>
#include <stdexcept>

// ────────────── Token 类型 ──────────────

enum class TokType {
    Number, Plus, Minus, Mul, Div, Pow,
    LParen, RParen, Comma, Func, Const, Eof
};

struct Token {
    TokType type;
    double value;       // Number 时有效
    QString funcName;   // Func 时有效

    Token(TokType t, double v = 0, const QString& fn = "")
        : type(t), value(v), funcName(fn) {}
};

// ────────────── 词法分析器 ──────────────

class Lexer {
public:
    explicit Lexer(const QString& text);
    QVector<Token> tokenize();

private:
    QString m_text;
    int m_pos = 0;
    int m_length;

    QChar peek() const;
    QChar advance();
    void skipWhitespace();
    double scanNumber();
    QString scanIdentifier();
};

// ────────────── 语法分析 + 求值 ──────────────

class Parser {
public:
    explicit Parser(const QVector<Token>& tokens);
    double parse();

private:
    QVector<Token> m_tokens;
    int m_pos = 0;

    const Token& peek() const;
    Token advance();
    Token expect(TokType type);

    double expression();
    double term();
    double unary();
    double power();
    double primary();
    double parseFuncCall();
    static double dispatchSingle(const QString& funcName, double arg);

    static double checkedPow(double base, double exp);
};

// ────────────── 对外接口 ──────────────

class CalculatorEngine {
public:
    // 计算表达式，成功返回 true，result 存放结果
    // 失败返回 false，error 存放错误信息
    static bool evaluate(const QString& expr, double& result, QString& error);

    // 格式化结果：整数去掉 .0
    static QString formatResult(double value);
};

#endif // CALCULATORENGINE_H

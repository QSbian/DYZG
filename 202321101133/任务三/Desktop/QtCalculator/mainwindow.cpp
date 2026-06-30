#include "mainwindow.h"
#include "calculatorengine.h"
#include <QGridLayout>
#include <QVBoxLayout>
#include <QHBoxLayout>
#include <QGroupBox>
#include <QFont>
#include <QFrame>
#include <QMessageBox>
#include <QSizePolicy>

MainWindow::MainWindow(QWidget* parent)
    : QMainWindow(parent)
{
    setupUI();
}

void MainWindow::setupUI() {
    setWindowTitle("科学计算器");
    setMinimumSize(420, 560);

    // ── 中央 widget ──
    QWidget* central = new QWidget(this);
    setCentralWidget(central);

    QVBoxLayout* mainLayout = new QVBoxLayout(central);
    mainLayout->setSpacing(8);
    mainLayout->setContentsMargins(12, 12, 12, 12);

    // ── 显示区域 ──

    // 历史记录
    m_historyLabel = new QLabel(this);
    m_historyLabel->setAlignment(Qt::AlignRight | Qt::AlignVCenter);
    m_historyLabel->setStyleSheet(
        "QLabel { color: #888; font-size: 12px; }");
    m_historyLabel->setMinimumHeight(24);
    m_historyLabel->setWordWrap(true);
    mainLayout->addWidget(m_historyLabel);

    // 表达式输入框 — 黑色背景
    m_inputEdit = new QLineEdit(this);
    m_inputEdit->setAlignment(Qt::AlignRight);
    m_inputEdit->setStyleSheet(
        "QLineEdit {"
        "  font-size: 22px;"
        "  color: #ffffff;"
        "  padding: 10px;"
        "  border: 2px solid #555555;"
        "  border-radius: 8px;"
        "  background: #1e1e1e;"
        "}"
        "QLineEdit:focus {"
        "  border-color: #4a90d9;"
        "}");
    m_inputEdit->setPlaceholderText("输入表达式...");
    mainLayout->addWidget(m_inputEdit);

    // 结果显示
    m_resultLabel = new QLabel("0", this);
    m_resultLabel->setAlignment(Qt::AlignRight | Qt::AlignVCenter);
    m_resultLabel->setStyleSheet(
        "QLabel {"
        "  font-size: 36px;"
        "  font-weight: bold;"
        "  color: #ffffff;"
        "  padding: 10px;"
        "  background: #2a2a2a;"
        "  border-radius: 8px;"
        "  min-height: 60px;"
        "}");
    mainLayout->addWidget(m_resultLabel);

    // ── 按钮区域 ──
    QGroupBox* btnGroup = new QGroupBox(this);
    QGridLayout* grid = new QGridLayout(btnGroup);
    grid->setSpacing(6);

    // 按钮文字 → 对应插入的表达式文本
    struct BtnDef {
        const char* label;
        const char* insert;
        const char* styleClass;
    };

    // 按钮布局: [行, 列]
    // Row 0: C    CE   ⌫    ÷
    // Row 1: sin  cos  tan  ×
    // Row 2: ln   log  sqrt −
    // Row 3: (    )    ^    +
    // Row 4: 7    8    9    =
    // Row 5: 4    5    6    =
    // Row 6: 1    2    3    abs
    // Row 7: 0    .    pi   pow(

    // 普通数字按钮 — 白色底，focus 变蓝
    QString numStyle =
        "QPushButton {"
        "  font-size: 20px;"
        "  color: #2c2c2c;"
        "  background: #ffffff;"
        "  border: 1px solid #cccccc;"
        "  border-radius: 6px;"
        "  padding: 14px;"
        "}"
        "QPushButton:focus {"
        "  color: #ffffff;"
        "  background: #4a90d9;"
        "  border: 1px solid #4a90d9;"
        "}"
        "QPushButton:hover { background: #e8f0fe; color: #2c2c2c; }"
        "QPushButton:pressed { background: #357abd; color: #ffffff; }";

    // 运算符 — 白色底，focus 变蓝
    QString opStyle =
        "QPushButton {"
        "  font-size: 20px;"
        "  color: #2c2c2c;"
        "  background: #ffffff;"
        "  border: 1px solid #cccccc;"
        "  border-radius: 6px;"
        "  padding: 14px;"
        "}"
        "QPushButton:focus {"
        "  color: #ffffff;"
        "  background: #4a90d9;"
        "  border: 1px solid #4a90d9;"
        "}"
        "QPushButton:hover { background: #e8f0fe; color: #2c2c2c; }"
        "QPushButton:pressed { background: #357abd; color: #ffffff; }";

    // 功能按钮 — 白色底，focus 变蓝
    QString funcStyle =
        "QPushButton {"
        "  font-size: 16px;"
        "  color: #2c2c2c;"
        "  background: #ffffff;"
        "  border: 1px solid #cccccc;"
        "  border-radius: 6px;"
        "  padding: 14px;"
        "}"
        "QPushButton:focus {"
        "  color: #ffffff;"
        "  background: #4a90d9;"
        "  border: 1px solid #4a90d9;"
        "}"
        "QPushButton:hover { background: #e8f0fe; color: #2c2c2c; }"
        "QPushButton:pressed { background: #357abd; color: #ffffff; }";

    // 等号 — 白色底，focus 变蓝（与其余按钮统一）
    QString eqStyle =
        "QPushButton {"
        "  font-size: 20px;"
        "  color: #2c2c2c;"
        "  background: #ffffff;"
        "  border: 1px solid #cccccc;"
        "  border-radius: 6px;"
        "  padding: 14px;"
        "}"
        "QPushButton:focus {"
        "  color: #ffffff;"
        "  background: #4a90d9;"
        "  border: 1px solid #4a90d9;"
        "}"
        "QPushButton:hover { background: #e8f0fe; color: #2c2c2c; }"
        "QPushButton:pressed { background: #357abd; color: #ffffff; }";

    // 创建按钮的 lambda
    auto makeBtn = [&](const QString& label, const QString& insert,
                       const QString& style) -> QPushButton* {
        QPushButton* btn = new QPushButton(label, this);
        btn->setStyleSheet(style);
        btn->setSizeIncrement(QSizePolicy::Expanding, QSizePolicy::Expanding);
        btn->setProperty("insertText", insert);
        connect(btn, &QPushButton::clicked, this, &MainWindow::onButtonClicked);
        return btn;
    };

    // ── Row 0: 功能键 ──
    QPushButton* btnC = new QPushButton("C", this);
    btnC->setStyleSheet(funcStyle);
    connect(btnC, &QPushButton::clicked, this, &MainWindow::onClear);
    grid->addWidget(btnC, 0, 0);

    QPushButton* btnCE = new QPushButton("CE", this);
    btnCE->setStyleSheet(funcStyle);
    connect(btnCE, &QPushButton::clicked, this, &MainWindow::onClearAll);
    grid->addWidget(btnCE, 0, 1);

    QPushButton* btnBack = new QPushButton("⌫", this);
    btnBack->setStyleSheet(funcStyle);
    connect(btnBack, &QPushButton::clicked, this, &MainWindow::onBackspace);
    grid->addWidget(btnBack, 0, 2);

    grid->addWidget(makeBtn("÷", "/", opStyle), 0, 3);

    // ── Row 1: sin cos tan × ──
    grid->addWidget(makeBtn("sin", "sin(", funcStyle), 1, 0);
    grid->addWidget(makeBtn("cos", "cos(", funcStyle), 1, 1);
    grid->addWidget(makeBtn("tan", "tan(", funcStyle), 1, 2);
    grid->addWidget(makeBtn("×", "*", opStyle), 1, 3);

    // ── Row 2: ln log sqrt − ──
    grid->addWidget(makeBtn("ln", "ln(", funcStyle), 2, 0);
    grid->addWidget(makeBtn("log", "log(", funcStyle), 2, 1);
    grid->addWidget(makeBtn("√", "sqrt(", funcStyle), 2, 2);
    grid->addWidget(makeBtn("−", "-", opStyle), 2, 3);

    // ── Row 3: ( ) ^ + ──
    grid->addWidget(makeBtn("(", "(", numStyle), 3, 0);
    grid->addWidget(makeBtn(")", ")", numStyle), 3, 1);
    grid->addWidget(makeBtn("^", "^", numStyle), 3, 2);
    grid->addWidget(makeBtn("+", "+", opStyle), 3, 3);

    // ── Row 4: 7 8 9 = ──
    grid->addWidget(makeBtn("7", "7", numStyle), 4, 0);
    grid->addWidget(makeBtn("8", "8", numStyle), 4, 1);
    grid->addWidget(makeBtn("9", "9", numStyle), 4, 2);

    QPushButton* btnEq = new QPushButton("=", this);
    btnEq->setStyleSheet(eqStyle);
    connect(btnEq, &QPushButton::clicked, this, &MainWindow::onEquals);
    grid->addWidget(btnEq, 4, 3, 2, 1);  // 跨两行

    // ── Row 5: 4 5 6 ──
    grid->addWidget(makeBtn("4", "4", numStyle), 5, 0);
    grid->addWidget(makeBtn("5", "5", numStyle), 5, 1);
    grid->addWidget(makeBtn("6", "6", numStyle), 5, 2);

    // ── Row 6: 1 2 3 abs ──
    grid->addWidget(makeBtn("1", "1", numStyle), 6, 0);
    grid->addWidget(makeBtn("2", "2", numStyle), 6, 1);
    grid->addWidget(makeBtn("3", "3", numStyle), 6, 2);
    grid->addWidget(makeBtn("abs", "abs(", funcStyle), 6, 3);

    // ── Row 7: 0 . π pow( ──
    grid->addWidget(makeBtn("0", "0", numStyle), 7, 0);
    grid->addWidget(makeBtn(".", ".", numStyle), 7, 1);
    grid->addWidget(makeBtn("π", "pi", funcStyle), 7, 2);
    grid->addWidget(makeBtn("pow", "pow(", funcStyle), 7, 3);

    // 让按钮等宽
    for (int i = 0; i < grid->columnCount(); i++)
        grid->setColumnStretch(i, 1);
    for (int i = 0; i < grid->rowCount(); i++)
        grid->setRowStretch(i, 1);

    mainLayout->addWidget(btnGroup, 1);

    // ── 底部帮助按钮 ──
    QHBoxLayout* bottomLayout = new QHBoxLayout();
    QPushButton* btnHelp = new QPushButton("帮助", this);
    btnHelp->setStyleSheet(
        "QPushButton { font-size: 13px; padding: 6px 16px; }");
    connect(btnHelp, &QPushButton::clicked, this, &MainWindow::onHelp);
    bottomLayout->addStretch();
    bottomLayout->addWidget(btnHelp);
    mainLayout->addLayout(bottomLayout);

    // ── 信号：回车计算 ──
    connect(m_inputEdit, &QLineEdit::returnPressed, this, &MainWindow::onEquals);
}

void MainWindow::appendToExpression(const QString& text) {
    m_inputEdit->insert(text);
    m_inputEdit->setFocus();
}

void MainWindow::onButtonClicked() {
    QPushButton* btn = qobject_cast<QPushButton*>(sender());
    if (!btn) return;
    QString insert = btn->property("insertText").toString();
    appendToExpression(insert);
}

void MainWindow::onEquals() {
    QString expr = m_inputEdit->text().trimmed();
    if (expr.isEmpty()) return;

    double result;
    QString error;
    if (CalculatorEngine::evaluate(expr, result, error)) {
        QString resultStr = CalculatorEngine::formatResult(result);
        m_resultLabel->setText(resultStr);
        m_resultLabel->setStyleSheet(
            "QLabel {"
            "  font-size: 36px;"
            "  font-weight: bold;"
            "  color: #27ae60;"
            "  padding: 10px;"
            "  background: #2a2a2a;"
            "  border-radius: 8px;"
            "  min-height: 60px;"
            "}");

        // 添加历史
        m_history += expr + " = " + resultStr + "\n";
        if (m_history.length() > 300)
            m_history = m_history.right(300);
        m_historyLabel->setText(m_history.trimmed());

        // 结果可继续使用
        m_inputEdit->setText(resultStr);
        m_inputEdit->selectAll();
    } else {
        m_resultLabel->setText(error);
        m_resultLabel->setStyleSheet(
            "QLabel {"
            "  font-size: 18px;"
            "  font-weight: bold;"
            "  color: #e74c3c;"
            "  padding: 10px;"
            "  background: #3a1a1a;"
            "  border-radius: 8px;"
            "  min-height: 60px;"
            "}");
    }
    m_inputEdit->setFocus();
}

void MainWindow::onClear() {
    m_inputEdit->clear();
    m_resultLabel->setText("0");
    m_resultLabel->setStyleSheet(
        "QLabel {"
        "  font-size: 36px;"
        "  font-weight: bold;"
        "  color: #ffffff;"
        "  padding: 10px;"
        "  background: #2a2a2a;"
        "  border-radius: 8px;"
        "  min-height: 60px;"
        "}");
    m_inputEdit->setFocus();
}

void MainWindow::onClearAll() {
    m_inputEdit->clear();
    m_resultLabel->setText("0");
    m_resultLabel->setStyleSheet(
        "QLabel {"
        "  font-size: 36px;"
        "  font-weight: bold;"
        "  color: #ffffff;"
        "  padding: 10px;"
        "  background: #2a2a2a;"
        "  border-radius: 8px;"
        "  min-height: 60px;"
        "}");
    m_history.clear();
    m_historyLabel->clear();
    m_inputEdit->setFocus();
}

void MainWindow::onBackspace() {
    m_inputEdit->backspace();
    m_inputEdit->setFocus();
}

void MainWindow::onHelp() {
    QMessageBox::information(this, "使用帮助",
        "科学计算器使用说明：\n\n"
        "  运算符:  +  -  ×  ÷  ^\n"
        "  乘方:    2^3 → 8    pow(2, 3) → 8\n"
        "  开方:    sqrt(9) → 3    pow(9, 1/2) → 3\n"
        "  括号:    (1+2)*3 → 9\n"
        "  负数:    -3 + 5 → 2\n\n"
        "  三角函数:\n"
        "    sin(π/6) → 0.5    cos(0) → 1\n"
        "    tan(π/4) → 1\n"
        "  对数:\n"
        "    ln(1) → 0    log(100) → 2\n"
        "  其他:\n"
        "    abs(-5) → 5    sqrt(2) → 1.414...\n"
        "  常量:\n"
        "    π → 3.14159...    e → 2.71828...\n\n"
        "  pow(m, n): m 的 n 次方\n"
        "  pow 参数可留空，默认为 1\n\n"
        "  按回车键或 = 计算结果\n"
        "  C: 清除当前输入\n"
        "  CE: 全部清除\n"
        "  ⌫: 退格删除");
}

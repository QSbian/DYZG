#ifndef MAINWINDOW_H
#define MAINWINDOW_H

#include <QMainWindow>
#include <QLineEdit>
#include <QLabel>
#include <QPushButton>
#include <QVector>

class MainWindow : public QMainWindow {
    Q_OBJECT

public:
    explicit MainWindow(QWidget* parent = nullptr);

private slots:
    void onButtonClicked();
    void onEquals();
    void onClear();
    void onClearAll();
    void onBackspace();
    void onHelp();

private:
    void setupUI();
    void appendToExpression(const QString& text);

    QLineEdit*   m_inputEdit;     // 表达式输入框
    QLabel*      m_resultLabel;   // 结果显示
    QLabel*      m_historyLabel;  // 历史记录

    QString m_history;            // 累积历史文本
};

#endif // MAINWINDOW_H

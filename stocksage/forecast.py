"""小型岭回归及扩展窗口回测；标准化参数只来自训练窗口。"""
import math


def solve(matrix, vector):
    augmented = [row[:] + [value] for row, value in zip(matrix, vector)]
    size = len(vector)
    for column in range(size):
        pivot = max(range(column, size), key=lambda row: abs(augmented[row][column]))
        augmented[column], augmented[pivot] = augmented[pivot], augmented[column]
        divisor = augmented[column][column]
        if abs(divisor) < 1e-12:
            raise ValueError("模型矩阵奇异，请增加数据或正则化")
        augmented[column] = [value / divisor for value in augmented[column]]
        for row in range(size):
            if row != column:
                factor = augmented[row][column]
                augmented[row] = [a - factor * b for a, b in zip(augmented[row], augmented[column])]
    return [row[-1] for row in augmented]


def fit_predict(training, features, dimensions=6, alpha=8.0):
    if not training:
        raise ValueError("训练集不能为空")
    means = [sum(row["features"][j] for row in training) / len(training) for j in range(dimensions)]
    scales = [math.sqrt(sum((row["features"][j] - means[j]) ** 2 for row in training) / len(training)) or 1
              for j in range(dimensions)]
    def transform(values):
        return [1.0] + [(values[j] - means[j]) / scales[j] for j in range(dimensions)]
    x = [transform(row["features"]) for row in training]
    size = dimensions + 1
    matrix = [[sum(row[i] * row[j] for row in x) + (alpha if i == j and i else 0)
               for j in range(size)] for i in range(size)]
    vector = [sum(row[i] * item["target"] for row, item in zip(x, training)) for i in range(size)]
    weights = solve(matrix, vector)
    normalized = transform(features)
    contributions = [weights[j + 1] * normalized[j + 1] for j in range(dimensions)]
    return sum(a * b for a, b in zip(weights, normalized)), contributions, weights[0]


def metrics(actual, predicted):
    n = len(actual)
    nonzero = [(a, p) for a, p in zip(actual, predicted) if a != 0]
    return dict(mae=sum(abs(a - p) for a, p in zip(actual, predicted)) / n,
                rmse=math.sqrt(sum((a - p) ** 2 for a, p in zip(actual, predicted)) / n),
                direction_accuracy=sum((a > 0 and p > 0) or (a < 0 and p < 0) for a, p in nonzero) / len(nonzero) if nonzero else None,
                samples=n)


def evaluate(rows):
    labeled = [row for row in rows if row["target"] is not None]
    split = max(15, int(len(labeled) * .6))
    traces = []
    for index in range(split, len(labeled)):
        # 同一股票的连续交易日：训练标签在本次收盘预测时均已可观察。
        training, row = labeled[:index], labeled[index]
        fused, _, _ = fit_predict(training, row["features"])
        price_only, _, _ = fit_predict(training, row["features"], dimensions=4)
        traces.append(dict(date=row["date"], target_date=row["target_date"], actual=row["target"],
                           fused=fused, price_only=price_only, zero=0.0,
                           train_end=training[-1]["date"], train_label_end=training[-1]["target_date"]))
    if not traces:
        raise ValueError("可回测样本不足")
    predicted, contributions, intercept = fit_predict(labeled, rows[-1]["features"])
    actual = [row["actual"] for row in traces]
    return dict(method="岭回归 · 固定 α=8 · 扩展窗口逐日重训", target="当前收盘后预测下一交易日收盘收益率",
                metrics={name: metrics(actual, [row[name] for row in traces]) for name in ("zero", "price_only", "fused")},
                backtest=traces, next_return=predicted, as_of=rows[-1]["date"],
                contributions=contributions, intercept=intercept,
                residual_scale=metrics(actual, [row["fused"] for row in traces])["rmse"],
                train_samples=len(labeled), test_samples=len(traces),
                note="误差尺度为历史回测 RMSE，不是置信区间；合成数据结果不能推断真实投资效果。")

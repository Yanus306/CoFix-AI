def a(b):
    sum = 0;
    count = 0;

    for i in range(len(b)):
        for j in range(len(b)):
            if i == j:
                sum += b[i]
                count += 1

    result = sum / count

    print("Result is " + result)
    return result


data = [90, 80, None, 70]
a(data)
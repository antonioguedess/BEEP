with open('teste_final_1h.csv', 'rb') as f:
    data = f.read(500)
    print(f"Bytes lidos: {data}")
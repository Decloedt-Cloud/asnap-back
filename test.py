import torch
x = torch.rand(5, 3)
print(x)
print(f"CUDA available: {torch.cuda.is_available()}")
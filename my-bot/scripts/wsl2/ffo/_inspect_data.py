import numpy as np
import os

path = '/mnt/d/qlib/qlib_data/cn_data/features/sh600000/close.day.bin'
d = np.load(path, allow_pickle=True)
print('shape:', d.shape)
print('dtype:', d.dtype)
if d.ndim == 1:
    print('first 5:', d[:5])
elif d.ndim == 2:
    print('first 5 rows, 3 cols:', d[:5, :3])
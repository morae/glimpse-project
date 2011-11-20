
# Copyright (c) 2011 Mick Thomure
# All rights reserved.
#
# Please see the file COPYING in this distribution for usage terms.

#
# Functions for dealing with N-dimensional arrays.
#

import Image
import math
import numpy

def ArgMax(array):
  """Short-hand to find array indices containing the maximum value."""
  return numpy.transpose(numpy.nonzero(array == array.max()))

def ArgMin(array):
  """Short-hand to find array indices containing the minimum value."""
  return numpy.transpose(numpy.nonzero(array == array.min()))

def ScaleUnitNorm(x):
  """Scale elements of vector (in place), such that result has unit norm."""
  norm = numpy.linalg.norm(x)
#  norm = math.sqrt((x**2).sum())
  if norm == 0:
    x[:] = 1.0 / math.sqrt(x.size)
  else:
    x /= norm
  return x

def ArrayToGreyscaleImage(array, normalize = True):
  """Create a greyscale image from a 2D numpy array. Assumes range of input
     values contains 0."""
  if array.dtype != numpy.float32:
    array = array.astype(numpy.float32)
  if len(array.shape) > 2:
    # Stack bands vertically.
    array = array.reshape((-1, array.shape[-1]))
  if normalize:
    # Normalize array values to lie in [0, 255].
    max_val = max(abs(array.min()), array.max())
    # Map values to lie in [-.5, .5]
    array = array / (2 * max_val)       # makes copy of input
    # Map to [0, 1]
    array += 0.5
    # Map to [0, 255]
    array *= 255
  # Convert to unsigned chars
  array = numpy.asarray(array, dtype = numpy.uint8)
  return Image.fromarray(array, 'L')

def ArrayToRGBImage(array):
  return Image.fromarray(array, 'RGB')

def ArrayListToVector(arrays):
  """Convert list of numpy arrays to a single numpy vector."""
  assert len(arrays) > 0
  out_size = sum(a.size for a in arrays)
  out = numpy.empty((out_size,), arrays[0].dtype)
  offset = 0
  for a in arrays:
    out[offset : offset + a.size] = a.flat
    offset += a.size
  return out

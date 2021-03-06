import copy
import logging
import os
import sys
import time
import warnings

try:
   import cPickle as pickle
except:
   import pickle
import yaml

import numpy as np

with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    import matplotlib
    from matplotlib import pyplot as plt

import scipy.ndimage as ndimage

from hexrd import config
from hexrd.coreutil import initialize_experiment

def write_image(filename, arr, pts=None, minsize=None, **kwds):

    xsize, ysize = 1024, 1024

    fig = plt.figure(figsize=(10, 10), dpi=120)
    # plot the image
    ax = fig.gca()
    kwds['interpolation'] = kwds.get('interpolation', 'none')
    image = ax.imshow(arr, **kwds)
    ax.set_xlabel(r'X', fontsize='large')
    ax.set_ylabel(r'Y', fontsize='large')
    cbar = plt.colorbar(image)
    # plot any points
    if pts is not None:
        pts = np.asarray(pts)
        ax.plot(pts[:,1], pts[:,0], 'go', markersize=3)
        # resize (since adding points often adds padding)
        #ax.set_xlim(0, 2048)
        #ax.set_ylim(0, 2048)
    fig.savefig(filename, bbox_inches='tight', pad_inches=1./3.)
    fig.clf()
    plt.close()
#--

class GEBlob:
    def __init__(self, slice_x, slice_y, slice_z,
                 blob_label, blob_size):
        self.slice_x    = slice_x
        self.slice_y    = slice_y
        self.slice_z    = slice_z
        self.blob_label = blob_label
        self.blob_size  = blob_size

class GEPreProcessor:
    '''
        Pre-processing on GE files to smooth data,
        detect local maxima etc

        cfg = configuration dictionary read from a heXRD config file
        logger = a logger object to log progress/comments
    	gauss_sigma = std deviation for Gaussian smoothing on GE data
    	ge_data = GE raw data
    	ge_smooth_data = GE smoothed data with Gaussian
        ge_labeled_data = GE data with connected components labeled
        number_of_labels = Number of connected components in GE
        min_blob_size = min size of connected objects
        int_scale_factor = GE intensity is scaled by this.
        min_peak_separation = min_peak_separation
        blobs = An array of GEBlob (information about blobs)
    '''

    def __init__(self, cfg, logger,
                 gauss_sigma=3,
                 min_blob_size=125,
                 min_peak_separation=10):
    	self.cfg                 = cfg
        self.logger              = logger
    	self.gauss_sigma         = gauss_sigma
    	self.ge_data             = []
    	self.ge_smooth_data      = []
        self.ge_labeled_data     = []
        self.number_of_labels    = 0
        self.min_blob_size       = min_blob_size
        self.int_scale_factor    = 1
        self.min_peak_separation = min_peak_separation
        self.blobs               = []

        return

    def load_data(self):
        '''
            Read the config file and load appropriate GE2
            frames.
        '''
        cfg    = self.cfg
        logger = self.logger
        # process the data
        pd, reader, detector = initialize_experiment(cfg)
        n_frames = reader.getNFrames()
        logger.info("reading %d frames of data, storing values > %.1f", n_frames, cfg.fit_grains.threshold)
        frame_list = []
        for i in range(n_frames):
            frame = reader.read()
            frame_list.append(frame)

        frame_list = np.array(frame_list)
        frame_list[frame_list < cfg.fit_grains.threshold] = 0
        int_scale_factor = float(2**14)/float(np.amax(frame_list))
        frame_list = frame_list*int_scale_factor
        write_image('slice.png', frame_list[100, 100:400, 1350:1650], vmin=0)

        self.ge_data          = frame_list
        self.int_scale_factor = int_scale_factor

        return frame_list
    #--

    def smooth_data(self):
        '''
            Apply a Gaussian kernel to smooth data.
            This seems necessary because otherwise,
            the connected component detection later
            would detect many tiny spots. I prefer
            detecting larger regions that then can
            be split into subspots (= subgrains)
            using local maxima finding.
        '''
        cfg         = self.cfg
        logger      = self.logger
        ge_data     = self.ge_data
        gauss_sigma = self.gauss_sigma
        logger.info("smoothing data with a Gaussian filter of sigma = %d", gauss_sigma)
        ge_data_smooth = ndimage.filters.gaussian_filter(ge_data,
                                                         gauss_sigma,
                                                         truncate=2)

        write_image('slice_smooth.png', ge_data[100, 100:400, 1350:1650], vmin=0)

        self.ge_smooth_data = ge_data_smooth

        return ge_data_smooth
    #--
    def find_blobs(self):
        '''
            Find connected componnets (spots)
        '''
        min_size       = self.min_blob_size
        ge_data_smooth = self.ge_smooth_data
        cfg            = self.cfg
        logger         = self.logger

        logger.info("detecting connected components")
        # Get a binary mask for connected-component detection (1 = our blobs)
        ge_mask = ge_data_smooth > cfg.fit_grains.threshold
        # Label each connected component (spot)
        label_ge, number_of_labels = ndimage.label(ge_mask)

        logger.info("found %d connected components", number_of_labels)
        ge_labeled = np.amax(label_ge, 0)
        write_image('slice_labeled.png', ge_labeled, vmin=0)
        # Get size of each blob by counting number of pixels with the same label
        blob_sizes = ndimage.sum(ge_mask, label_ge, range(number_of_labels + 1))
        blob_labels = np.unique(label_ge)
        # Loop over all detected regions and filter based on size/aspect ratio
        # Save filtered blob information in an array 'blobs' of GEBlob object
        blobs = []
        for label_num, blob_size in zip(blob_labels, blob_sizes):
            # Label 0 is for the whole image background. Do not want.
            if label_num == 0:
                continue
            # Total pixels in blob < min_size? Move on
            if blob_size < min_size:
                continue
            # Get the minimal region of interest (ROI) for the blob
            slice_x, slice_y, slice_z = ndimage.find_objects(label_ge==label_num)[0]
            bbox = [slice_x.stop -  slice_x.start, slice_y.stop -  slice_y.start, slice_z.stop -  slice_z.start]
            # Is any of roi bounding box dim < min? Move on
            if(min(bbox) < (min_size ** (1.0/3.0))):
                continue

            # This looks like a legit spot. Add to blobs array
            blobs.append(GEBlob(slice_x, slice_y, slice_z, label_num, blob_size))

        ge_labeled = np.amax(label_ge, 0)

        write_image('slice_labeled_cleaned.png', ge_labeled, vmin=0)
        logger.info("after cleaning, %d connected components", len(blobs))

        self.ge_labeled_data  = label_ge
        self.number_of_labels = number_of_labels
        self.blob_sizes       = blob_sizes
        self.blobs            = blobs
        pickle.dump(blobs, open('ge_blobs.cpl', 'wb'))

        return label_ge
    #--
    def find_local_maxima(self):
        '''
            Find local maxima in each of the detected blobs
        '''
        ge_labeled_data     = self.ge_labeled_data
        ge_data             = self.ge_data
        cfg                 = self.cfg
        logger              = self.logger
        int_scale_factor    = self.int_scale_factor
        number_of_labels    = self.number_of_labels
        min_peak_separation = self.min_peak_separation
        blobs               = self.blobs

        logger.info("Detecting local maxima")

        for blob in blobs:
            slice_x, slice_y, slice_z = blob.slice_x, blob.slice_y, blob.slice_z
            label_num = blob.blob_label
            roi = ge_data[slice_x, slice_y, slice_z]
            write_image('roi' + str(label_num) + '.png', np.amax(roi, 0), vmin=0)

            max_points = roi == ndimage.maximum_filter(roi, min_peak_separation,
                                                       mode='nearest', cval=0)
            max_points[roi < int_scale_factor*cfg.fit_grains.threshold]
            roi[np.logical_not(max_points)] = 0

            write_image('roi' + str(label_num) + '_max.png', np.amax(roi, 0), vmin=0)

        return blobs

import sys
import scanpy.api as sc
import pandas as pd
sys.path.insert(0,'/n/home13/seichhorn/Python_code/MERCluster/utils')
import scanpy_helpers
import numpy as np
import os

sc.settings.verbosity = 3  # verbosity: errors (0), warnings (1), info (2), hints (3)
sc.settings.set_figure_params(dpi=150)  # low dpi (dots per inch) yields small inline figures
sc.logging.print_versions()


class Experiment:
	'''provides a class to contain raw data presented as either an AnnData object or path to AnnData object and wrappers around scanpy methods for data processing/clustering

methods:

filter - trims a dataset based on counts and/or genes present in each cell, extent of mitochondrial expression, and removes genes present in a small number of cells
selectVaraibleGenes - chooses variable genes based on a supplied list or dispersion
processData - logs, regresses, zscores data
selectPCs - chooses the number of PCs to use for subsequent methods
computeNeighbors - calculates a kNN graph and supplies edge weights based on jaccard index of nodes
cluster - runs louvain community detection 
bootstrapCells - subsamples the dataset

	'''
	def __init__(self, dataset, output):
		if type(dataset) == str:
			self.dataset = sc.read_h5ad(dataset)
		else:
			self.dataset = dataset
		self.output = output

	def filter(self, verbose=True, countsPercentileCutoffs=[0.0,1.0], genesPercentileCutoffs=[0.0,1.0], mitoPercentileMax=None, geneMin=10):

		if verbose:
				print('original dataset contains {} cells and {} genes'.format(self.dataset.X.shape[0],self.dataset.X.shape[1]))
		
		totalCells = self.dataset.X.shape[0]

		if not genesPercentileCutoffs == [0.0,1.0]:
			sc.pp.filter_cells(self.dataset.obs, min_genes=0)
		
			minGeneCut = self.dataset.obs.n_genes.quantile(q=genesPercentileCutoffs[0])
			maxGeneCut = self.dataset.obs.n_genes.quantile(q=genesPercentileCutoffs[1])

			sc.pp.filter_cells(self.dataset, min_genes=minGeneCut)
			sc.pp.filter_cells(self.dataset, max_genes=maxGeneCut)

		if not countsPercentileCutoffs == [0.0,1.0]:
			self.dataset.obs['n_counts'] = self.dataset.X.sum(1)

			minCountsCut = self.dataset.obs.n_counts.quantile(q=countsPercentileCutoffs[0])
			maxCountsCut = self.dataset.obs.n_counts.quantile(q=countsPercentileCutoffs[1])


			sc.pp.filter_cells(self.dataset, min_counts=minCountsCut)
			sc.pp.filter_cells(self.dataset, max_counts=maxCountsCut)
		
		if not int(geneMin) == 0:
			sc.pp.filter_genes(self.dataset, min_cells=geneMin)

		cellsCutOnCounts = self.dataset.X.shape[0]

		if not mitoPercentileMax == None:
			mito_genes = [name for name in self.dataset.var_names if name.startswith('mt-')]
			# for each cell compute fraction of counts in mito genes vs. all genes
			# the `.A1` is only necessary as X is sparse to transform to a dense array after summing
			self.dataset.obs['percent_mito'] = np.sum(
			    self.dataset[:, mito_genes].X, axis=1) / np.sum(self.dataset.X, axis=1)

			self.dataset = self.dataset[self.dataset.obs['percent_mito']<mitoPercentileMax]
			
			cellsCutOnMito = self.dataset.X.shape[0]

		else:
			cellsCutOnMito = cellsCutOnCounts

		if verbose:
			print('filtered dataset contains {} cells and {} genes, {} removed due to counts/genes cutoff and {} removed due to mitochondrial cutoff'.format(self.dataset.X.shape[0],self.dataset.X.shape[1], totalCells - cellsCutOnCounts, cellsCutOnCounts - cellsCutOnMito))



	def selectVaraibleGenes(self, preselectedGenesFile=None, dispersionMin=0.025, dispersionMax=4.0, dispersionThreshold=0.5):
		sc.pp.normalize_per_cell(self.dataset)
		if preselectedGenesFile == None:
			filter_result = sc.pp.filter_genes_dispersion(self.dataset.X, min_mean=dispersionMin, max_mean=dispersionMax, min_disp=dispersionThreshold)
		
			#Cutting the dataset to just the variable genes
			self.dataset = self.dataset[:,filter_result.gene_subset]

		else:
			preselectedGenes = pd.read_csv(preselectedGenesFile,header = None)[0].values.tolist()
			self.dataset = self.dataset[:,preselectedGenes]


	def processData(self, regressOut=['n_counts','percent_mito'], zscoreMax=6):
		sc.pp.log1p(self.dataset)

		if 'n_counts' in regressOut:
				if 'n_counts' not in self.dataset.obs.columns.values.tolist():
					self.dataset.obs['n_counts'] = self.dataset.X.sum(1)
		if 'percent_mito' in regressOut:
			if 'percent_mito' not in self.dataset.obs.columns.values.tolist():
				mito_genes = [name for name in self.dataset.var_names if name.startswith('mt-')]
				self.dataset.obs['percent_mito'] = np.sum(self.dataset[:, mito_genes].X, axis=1) / np.sum(self.dataset.X, axis=1)

		sc.pp.regress_out(self.dataset, regressOut)
		sc.pp.scale(self.dataset, max_value= zscoreMax)

	def selectPCs(self):
		maxPCs = int(np.min(self.dataset.X.shape)) - 1
		if maxPCs < 200:
			pcsToCalc = maxPCs
		else:
			pcsToCalc = 200
		sc.tl.pca(self.dataset, svd_solver = 'arpack', n_comps = pcsToCalc)

		#Shuffling the dataframe 10 times, each time calculate the PCs and take the variance explained of the first PC.
		randomVariance = []
		for i in range(10):
			shuffled = sc.AnnData(scanpy_helpers.shuffler(pd.DataFrame(self.dataset.X)))
			sc.tl.pca(shuffled,svd_solver = 'arpack')
			randomVariance.append(shuffled.uns['pca']['variance'][0])

		#Use only PCs that explain more variance than the random dataframe
		pcsToUse = len(self.dataset.uns['pca']['variance'][self.dataset.uns['pca']['variance']>np.median(randomVariance)])
		print('Using {} PCs'.format(pcsToUse))
		self.pcsToUse = pcsToUse

	def computeNeighbors(self, kValue=12, usePCA=True):
		self.kValue = kValue
		if usePCA:
			sc.pp.neighbors(self.dataset, n_neighbors=int(kValue), n_pcs = self.pcsToUse)
		else:
			sc.pp.neighbors(self.dataset, n_neighbors=int(kValue), n_pcs = 0)

		self.dataset.uns['neighbors']['connectivities'] = scanpy_helpers.neighbor_graph(scanpy_helpers.jaccard_kernel,self.dataset.uns['neighbors']['connectivities'])

	def cluster(self, resolution=1.0, clusterMin=10, trackIterations=False):
		self.resolution = resolution
		pathToOutput = '{}clustering/'.format(self.output)
		if not os.path.exists(pathToOutput):
			os.makedirs(pathToOutput)
		if trackIterations:
			pathToFullOutput = '{}clustering/iterationTracking/'.format(self.output)
			if not os.path.exists(pathToFullOutput):
				os.makedirs(pathToFullOutput)
			adjacency = self.dataset.uns['neighbors']['connectivities']
			g = sc.utils.get_igraph_from_adjacency(adjacency, directed=False)

			import louvain

			optimiser = louvain.Optimiser()
			tracking = []
			partition = louvain.RBConfigurationVertexPartition(g,weights = 'weight', resolution_parameter = resolution)
			partition_agg = partition.aggregate_partition()
			print(partition.summary())

			diff = optimiser.move_nodes(partition_agg)
			while diff > 0.0:
			    partition.from_coarse_partition(partition_agg)
			    partition_agg = partition_agg.aggregate_partition()
			    tracking.append(partition.membership)
			    print(partition_agg.summary())
			    diff = optimiser.move_nodes(partition_agg)

			df = pd.DataFrame(tracking,columns = self.dataset.obs.index).T

			df.to_csv(pathToFullOutput+'kValue_{}_resolution_{}'.format(self.kValue, int(self.resolution)),sep = '\t')

			clustering = scanpy_helpers.minimum_cluster_size(df.iloc[:,[-1]].copy(deep=True), min_size = clusterMin)
			clustering.columns = ['kValue_{}_resolution_{}'.format(kValue,int(resolution))]
			print('Clustering yields {} clusters with at least {} cells'.format(clustering['kValue_{}_resolution_{}'.format(kValue,int(resolution))].unique().max(),min_size))
		
			clustering.to_csv(pathToOutput+'kValue_{}_resolution_{}'.format(self.kValue, int(self.resolution)),sep = '\t')

		else:
			sc.tl.louvain(self.dataset, resolution = resolution, use_weights = True)

			clustering = pd.DataFrame(self.dataset.obs['louvain'])
			clustering = scanpy_helpers.minimum_cluster_size(clustering)
			clustering.columns = ['kValue_{}_resolution_{}'.format(kValue,int(resolution))]
			print('Clustering yields {} clusters with at least {} cells'.format(clustering['kValue_{}_resolution_{}'.format(kValue,int(resolution))].unique().max(),min_size))
		
			clustering.to_csv(pathToOutput+'kValue_{}_resolution_{}'.format(self.kValue, int(self.resolution)),sep = '\t')

	def bootstrapCells(self, frac = 0.8):
		sampleDF = pd.DataFrame(self.dataset.X, index = self.dataset.obs.index, columns = self.dataset.var.index)
		downSample = sampleDF.sample(frac=frac)

		downSampleAD = sc.AnnData(downSample.values)
		downSampleAD.obs.index = downSample.index.values.tolist()
		downSampleAD.var.index = downSample.columns.values.tolist()
		
		downSampleAD.obs = self.dataset.obs.loc[downSampleAD.obs.index,:]

		self.dataset = downSampleAD




















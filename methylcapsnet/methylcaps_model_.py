import pandas as pd
from pymethylprocess.MethylationDataTypes import MethylationArray
from sklearn.metrics import mean_absolute_error, r2_score
import warnings
warnings.filterwarnings("ignore")
from pybedtools import BedTool
import numpy as np
from functools import reduce
from torch.utils.data import Dataset, DataLoader
import torch
from torch import nn
from torch.autograd import Variable
from torch.nn import functional as F
import os
import pysnooper
import argparse
import pickle
from sklearn.metrics import classification_report
import click
import methylcapsnet
from methylcapsnet.methylcaps_data_models import *
import sqlite3
import os
RANDOM_SEED=42
np.random.seed(RANDOM_SEED)
torch.manual_seed(RANDOM_SEED)
torch.backends.cudnn.deterministic = True
torch.backends.cudnn.benchmark = False
methylcaps_dir=os.path.dirname(methylcapsnet.__file__)
annotations450 = os.path.abspath(os.path.join(methylcaps_dir, 'data/450kannotations.bed'))
hg19 = os.path.abspath(os.path.join(methylcaps_dir, 'data/hg19.genome'))
selected_caps_file = os.path.abspath(os.path.join(methylcaps_dir, 'data/selected_capsules.p'))

#@pysnooper.snoop('get_mod.log')
def get_binned_modules(ma=None,a=annotations450,b='lola_vignette_data/activeDHS_universe.bed', include_last=False, min_capsule_len=2000):
	allcpgs=ma.beta.columns.values
	df=BedTool(a).to_dataframe()
	df.iloc[:,0]=df.iloc[:,0].astype(str).map(lambda x: 'chr'+x.split('.')[0])
	df=df.set_index('name').loc[list(ma.beta)].reset_index().iloc[:,[1,2,3,0]]
	df_bed=pd.read_table(b,header=None)
	df_bed['features']=np.arange(df_bed.shape[0])
	df_bed=df_bed.iloc[:,[0,1,2,-1]]
	b=BedTool.from_dataframe(df)
	a=BedTool.from_dataframe(df_bed)#('lola_vignette_data/activeDHS_universe.bed')
	c=a.intersect(b,wa=True,wb=True).sort()
	d=c.groupby(g=[1,2,3,4],c=(8,8),o=('count','distinct'))
	df2=d.to_dataframe()
	df3=df2.loc[df2.iloc[:,-2]>min_capsule_len]
	modules = [cpgs.split(',') for cpgs in df3.iloc[:,-1].values]
	modulecpgs=np.array(list(set(list(reduce(lambda x,y:x+y,modules)))))
	if include_last:
		missing_cpgs=np.setdiff1d(allcpgs,modulecpgs).tolist()
	final_modules = modules+([missing_cpgs] if include_last else [])
	module_names=(df3.iloc[:,0]+'_'+df3.iloc[:,1].astype(str)+'_'+df3.iloc[:,2].astype(str)).tolist()
	return final_modules,modulecpgs,module_names

def return_custom_capsules(ma=None,capsule_file=selected_caps_file, capsule_sets=['all'], min_capsule_len=2000, include_last=False):
	allcpgs=ma.beta.columns.values
	caps_dict=pickle.load(open(capsule_file,'rb'))
	capsules={}
	if 'all' in capsule_sets:
		capsule_sets=list(caps_dict.keys())
	for caps_set in capsule_sets:
		for capsule in caps_dict[caps_set]:
			capsules[capsule]=np.intersect1d(caps_dict[caps_set][capsule],allcpgs).tolist()
	capsules={capsule:capsules[capsule] for capsule in capsules if len(capsules[capsule])>=min_capsule_len}
	modules = [capsules[capsule] for capsule in capsules]
	modulecpgs=np.array(list(set(list(reduce(lambda x,y:x+y,modules)))))
	module_names=list(capsules.keys())#(df3.iloc[:,0]+'_'+df3.iloc[:,1].astype(str)+'_'+df3.iloc[:,2].astype(str)).tolist()
	return modules,modulecpgs,module_names


#@pysnooper.snoop('train.log')
def model_capsnet_(train_methyl_array,
					val_methyl_array,
					interest_col,
					n_epochs,
					n_bins,
					bin_len,
					min_capsule_len,
					primary_caps_out_len,
					caps_out_len,
					hidden_topology,
					gamma,
					decoder_topology,
					learning_rate,
					routing_iterations,
					overlap,
					custom_loss,
					gamma2,
					job,
					capsule_choice,
					custom_capsule_file='',
					test_methyl_array='',
					predict=False):

	capsule_choice=list(capsule_choice)
	#custom_capsule_file=list(custom_capsule_file)
	hlt_list=filter(None,hidden_topology.split(','))
	if hlt_list:
		hidden_topology=list(map(int,hlt_list))
	else:
		hidden_topology=[]
	hlt_list=filter(None,decoder_topology.split(','))
	if hlt_list:
		decoder_topology=list(map(int,hlt_list))
	else:
		decoder_topology=[]

	hidden_caps_layers=[]
	include_last=False

	ma=MethylationArray.from_pickle(train_methyl_array)
	ma_v=MethylationArray.from_pickle(val_methyl_array)
	if test_methyl_array and predict:
		ma_t=MethylationArray.from_pickle(test_methyl_array)


	try:
		ma.remove_na_samples(interest_col)
		ma_v.remove_na_samples(interest_col)
		if test_methyl_array and predict:
			ma_t.remove_na_samples(interest_col)
	except:
		pass

	capsules,finalcpgs,capsule_names=[],[],[]
	annotation_file=annotations450
	if 'genomic_binned' in capsule_choice:
		overlap=int(overlap*bin_len)
		genome_file=hg19
		gname=genome_file.split('.')[0]

		if not os.path.exists('{}.{}.overlap.{}.bed'.format(gname,bin_len,overlap)):
			BedTool(genome_file).makewindows(g=genome_file,w=bin_len,s=bin_len-overlap).saveas('{}.{}.overlap.{}.bed'.format(gname,bin_len,overlap))#.to_dataframe().shape
		#print('LEN_MODULES',len(final_modules))
		final_modules,modulecpgs,module_names=get_binned_modules(ma=ma,a=annotation_file,b='{}.{}.overlap.{}.bed'.format(gname,bin_len,overlap),include_last=include_last, min_capsule_len=min_capsule_len)
		capsules.extend(final_modules)
		finalcpgs.extend(modulecpgs)
		capsule_names.extend(module_names)

	if 'custom_bed' in capsule_choice:
		final_modules,modulecpgs,module_names=get_binned_modules(ma=ma,a=annotation_file,b=custom_capsule_file,include_last=include_last, min_capsule_len=min_capsule_len)
		capsules.extend(final_modules)
		finalcpgs.extend(modulecpgs)
		capsule_names.extend(module_names)

	if 'custom_set' in capsule_choice:
		final_modules,modulecpgs,module_names=return_custom_capsules(ma=ma,capsule_file=custom_capsule_file, capsule_sets=['all'], min_capsule_len=min_capsule_len, include_last=include_last)
		capsules.extend(final_modules)
		finalcpgs.extend(modulecpgs)
		capsule_names.extend(module_names)

	selected_sets=np.intersect1d(['UCSC_RefGene_Accession', 'UCSC_RefGene_Group', 'UCSC_CpG_Islands_Name', 'Relation_to_UCSC_CpG_Island', 'Phantom', 'DMR', 'Enhancer', 'HMM_Island', 'Regulatory_Feature_Name', 'Regulatory_Feature_Group', 'DHS'],capsule_choice).tolist()
	if selected_sets:
		final_modules,modulecpgs,module_names=return_custom_capsules(ma=ma,capsule_file=selected_caps_file, capsule_sets=selected_sets, min_capsule_len=min_capsule_len, include_last=include_last)
		capsules.extend(final_modules)
		finalcpgs.extend(modulecpgs)
		capsule_names.extend(module_names)

	final_modules=capsules
	modulecpgs=list(set(finalcpgs))
	module_names=capsule_names

	del capsules,finalcpgs,capsule_names

	if not include_last:
		ma.beta=ma.beta.loc[:,modulecpgs]
		ma_v.beta=ma_v.beta.loc[:,modulecpgs]
		if test_methyl_array and predict:
			ma_t.beta=ma_t.beta.loc[:,modulecpgs]
	# https://github.com/higgsfield/Capsule-Network-Tutorial/blob/master/Capsule%20Network.ipynb
	original_interest_col=interest_col
	if n_bins:
		new_interest_col=interest_col+'_binned'
		ma.pheno.loc[:,new_interest_col],bins=pd.cut(ma.pheno[interest_col],bins=n_bins,retbins=True)
		ma_v.pheno.loc[:,new_interest_col],_=pd.cut(ma_v.pheno[interest_col],bins=bins,retbins=True)
		if test_methyl_array and predict:
			ma_t.pheno.loc[:,new_interest_col],_=pd.cut(ma_t.pheno[interest_col],bins=bins,retbins=True)
		interest_col=new_interest_col

	datasets=dict()

	datasets['train']=MethylationDataset(ma,interest_col,modules=final_modules, module_names=module_names, original_interest_col=original_interest_col)
	datasets['val']=MethylationDataset(ma_v,interest_col,modules=final_modules, module_names=module_names, original_interest_col=original_interest_col)
	if test_methyl_array and predict:
		datasets['test']=MethylationDataset(ma_t,interest_col,modules=final_modules, module_names=module_names, original_interest_col=original_interest_col)

	dataloaders=dict()

	dataloaders['train']=DataLoader(dataloaders['train'],batch_size=16,shuffle=True,num_workers=8, drop_last=True)
	dataloaders['val']=DataLoader(dataloaders['val'],batch_size=16,shuffle=False,num_workers=8, drop_last=False)
	n_primary=len(final_modules)
	if test_methyl_array and predict:
		dataloaders['test']=DataLoader(dataloaders['test'],batch_size=16,shuffle=False,num_workers=8, drop_last=False)
		capsnet=torch.load('capsnet_model.pkl')

	else:
		n_inputs=list(map(len,final_modules))


		primary_caps = PrimaryCaps(modules=final_modules,hidden_topology=hidden_topology,n_output=primary_caps_out_len)
		hidden_caps = []
		n_out_caps=len(datasets['train'].y_unique)

		output_caps = CapsLayer(n_out_caps,n_primary,primary_caps_out_len,caps_out_len,routing_iterations=routing_iterations)
		decoder = Decoder(n_out_caps*caps_out_len,len(list(ma.beta)),decoder_topology)
		capsnet = CapsNet(primary_caps, hidden_caps, output_caps, decoder, gamma=gamma)

	if torch.cuda.is_available():
		capsnet=capsnet.cuda()


	# extract all c_ij for all layers across all batches, or just last batch

	trainer=Trainer(capsnet=capsnet,
					validation_dataloader=dataloaders['val'],
					n_epochs=n_epochs,
					lr=learning_rate,
					n_primary=n_primary,
					custom_loss=custom_loss,
					gamma2=gamma2)

	if not predict:
		try:
			#assert 1==2
			trainer.fit(dataloader=dataloaders['train'])
			val_loss=min(trainer.val_losses)
		except Exception as e:
			print(e)
			val_loss=-2

		torch.save(trainer.capsnet,'capsnet_model.pkl')

		with sqlite3.connect('jobs.db', check_same_thread=False) as conn:
			pd.DataFrame([job,val_loss],index=['job','val_loss'],columns=[0]).T.to_sql('val_loss',conn,if_exists='append')
	else:
		if test_methyl_array:
			Y=trainer.predict(dataloaders['test'])
			pickle.dump(Y,'predictions.pkl')
			val_loss=-1
	#print(val_loss)
	# print([min(trainer.val_losses),n_epochs,
	# 		n_bins,
	# 		bin_len,
	# 		min_capsule_len,
	# 		primary_caps_out_len,
	# 		caps_out_len,
	# 		hidden_topology,
	# 		gamma,
	# 		decoder_topology,
	# 		learning_rate,
	# 		routing_iterations])



	return val_loss

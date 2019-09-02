from threading import Thread
import dask
import chocolate as choco
import time
import numpy as np, pandas as pd
import subprocess
import sqlite3
import click
from methylnet.torque_jobs import assemble_run_torque
#from dask.distributed import Client, as_completed
from methylcaps_train_ import *

CONTEXT_SETTINGS = dict(help_option_names=['-h','--help'], max_content_width=90)

@click.group(context_settings= CONTEXT_SETTINGS)
@click.version_option(version='0.1')
def hypscan():
	pass

class MonitorJobs(Thread):
	def __init__(self, start_time, delay, end_time, job):
		super(MonitorJobs, self).__init__()
		self.stopped = False
		self.start_time = start_time
		self.end_time = end_time
		self.delay = delay # Time between calls to GPUtil
		self.val_loss = -1
		self.job = job
		self.start()

	def search_jobs(self):
		#print('read jobs')
		with sqlite3.connect('jobs.db', check_same_thread=False) as conn:
			# c=conn.cursor()
			# c.execute("""SELECT count(name) FROM sqlite_master WHERE type='table' AND name='val_loss';""")
			# if c.fetchone()[0]==1:
			try:
				df=pd.read_sql("select * from 'val_loss'",conn).set_index('job')
				#print(self.job in list(df.index))
				if self.job in list(df.index):
					self.val_loss=df.loc[self.job,'val_loss']
				else:
					self.val_loss=-1
			except Exception as e:
				#print(e)
				self.val_loss=-1
		# else:
		# 	self.val_loss=-1
		# del c
		# try:
		# 	df=pd.read_pickle('jobs.p').set_index('job')
		# 	if self.job in list(df.index):
		# 		self.val_loss=df.loc[self.job,'val_loss']
		# 	else:
		# 		self.val_loss=-1
		# except:
		# 	self.val_loss=-1

	def run(self):
		time_from_start = 0.
		while time_from_start <= self.end_time and self.val_loss==-1:
			self.search_jobs()
			time.sleep(self.delay)
		self.stop()

	def stop(self):
		self.stopped = True

	def return_val_loss(self):
		return self.val_loss

def return_val_loss(command, torque, total_time, delay_time, job, gpu, additional_command, additional_options):
	print(int(np.ceil(total_time/60.)))
	if torque:
		assemble_run_torque(command, use_gpu=gpu, additions=additional_command, queue='gpuq' if gpu else "normal", time=int(np.ceil(total_time/60.)), ngpu=1, additional_options=additional_options)
	else:
		subprocess.call(command,shell=True)

	total_time*= 60.
	start_time = time.time()

	monitor = MonitorJobs(start_time, delay_time, total_time, job=job)

	monitor.run()

	while not monitor.stopped:
		time.sleep(delay_time)

	val_loss = monitor.return_val_loss()

	return val_loss

@hypscan.command()
@click.option('-i', '--train_methyl_array', default='./train_val_test_sets/train_methyl_array.pkl', help='Input database for beta and phenotype data.', type=click.Path(exists=False), show_default=True)
@click.option('-v', '--val_methyl_array', default='./train_val_test_sets/val_methyl_array.pkl', help='Test database for beta and phenotype data.', type=click.Path(exists=False), show_default=True)
@click.option('-ic', '--interest_col', default='disease', help='Specify column looking to make predictions on.', show_default=True)
@click.option('-nb', '--n_bins', default=0, help='Number of bins if column is continuous variable.', show_default=True)
@click.option('-cl', '--custom_loss', default='none', help='Specify custom loss function.', show_default=True, type=click.Choice(['none','cox']))
@click.option('-t', '--torque', is_flag=True, help='Submit jobs on torque.')
@click.option('-s', '--search_strategy', default='bayes', help='Search strategy.', type=click.Choice(['bayes','random','quasi']))
@click.option('-tt', '--total_time', default=60, help='Total time to run each job in minutes.', show_default=True)
@click.option('-dt', '--delay_time', default=60, help='Total time to wait before searching for output job in seconds.', show_default=True)
@click.option('-gpu', '--gpu', is_flag=True, help='If torque submit, which gpu to use.')
@click.option('-a', '--additional_command', default='', help='Additional command to input for torque run.', type=click.Path(exists=False))
@click.option('-ao', '--additional_options', default='', help='Additional options to input for torque run.', type=click.Path(exists=False))
@click.option('-u', '--update', is_flag=True, help='Update in script.')
@click.option('-ne', '--n_epochs', default=10, help='Number of epochs. Setting to 0 induces scan of epochs.')
@click.option('-j', '--job', default=42, help='Job name.')
def hyperparameter_job(train_methyl_array,
						val_methyl_array,
						interest_col,
						n_bins,
						custom_loss,
						torque,
						search_strategy,
						total_time,
						delay_time,
						gpu,
						additional_command,
						additional_options,
						update,
						n_epochs,
						job):

	additional_params=dict(train_methyl_array=train_methyl_array,
							val_methyl_array=val_methyl_array,
							interest_col=interest_col,
							n_bins=n_bins,
							custom_loss=custom_loss,
							job=job)

	if n_epochs:
		additional_params['n_epochs']=n_epochs

	def score_loss(params):
		#job=np.random.randint(0,1000000)

		params['hidden_topology']=','.join([str(params['el{}s'.format(j)]) for j in range(params['nehl'])])
		params['decoder_topology']=','.join([str(params['dl{}s'.format(j)]) for j in range(params['ndhl'])])

		del_params=[str(params['el{}s'.format(j)]) for j in range(params['nehl'])]+[str(params['dl{}s'.format(j)]) for j in range(params['ndhl'])]

		# for k in list(params.keys()):
		# 	if k.endswith('_size'):
		# 		del params[k]
		for param in del_params:
			del params[param]

		del params['nehl'], params['ndhl']

		params.update(additional_params)

		print(params)

		if update:

			val_loss = train_capsnet_(**params)

		else:

			command='{} python methylcapsnet_cli.py train_capsnet {} || python methylcapsnet_cli.py report_loss -j {}'.format('CUDA_VISIBLE_DEVICES=0' if gpu and not torque else '',' '.join(['--{} {}'.format(k,v) for k,v in params.items() if v]),params['job'])#,'&' if not torque else '')

			val_loss = return_val_loss(command, torque, total_time, delay_time, job, gpu, additional_command, additional_options)

		return val_loss


	grid=dict(n_epochs=choco.quantized_uniform(low=10, high=50, step=10),
				bin_len=choco.quantized_uniform(low=100000, high=1000000, step=100000),
				min_capsule_len=choco.quantized_uniform(low=50, high=500, step=50),
				primary_caps_out_len=choco.quantized_uniform(low=10, high=100, step=5),
				caps_out_len=choco.quantized_uniform(low=10, high=100, step=5),
				nehl={i: {'el{}s'.format(j):choco.quantized_uniform(10,100,10) for j in range(i+1)} for i in range(3)},
				#hidden_topology=,
				gamma=choco.quantized_log(-5,-1,1,10),
				ndhl={i: {'dl{}s'.format(j):choco.quantized_uniform(10,100,10) for j in range(i+1)} for i in range(3)},
				#decoder_topology=,
				learning_rate=choco.quantized_log(-5,-1,1,10),
				routing_iterations=choco.quantized_uniform(low=2, high=6, step=1),
				overlap=choco.quantized_uniform(low=0., high=.9, step=.1),
				gamma2=choco.quantized_log(-5,-1,1,10)
			) # ADD BATCH SIZE

	if n_epochs:
		grid.pop('n_epochs')

	optimization_method = 'bayes'
	optimization_methods=['random','quasi','bayes']

	sampler_opts={}

	if optimization_method in ['random']:
		sampler_opts['random_state']=42
	elif optimization_method in ['quasi']:
		sampler_opts['seed']=42
		sampler_opts['skip']=3
	elif optimization_method in ['bayes']:
		sampler_opts['n_bootstrap']=10
		#sampler_opts['random_state']=42

	optimizer = dict(random=choco.Random,quasi=choco.QuasiRandom,bayes=choco.Bayes)[optimization_method]

	hyp_conn = choco.SQLiteConnection(url="sqlite:///hyperparameter_scan.db")

	sampler = optimizer(hyp_conn, grid, **sampler_opts)

	if 0 and optimization_method in ['bayes']:
		sampler.random_state=np.random.RandomState(42)

	token,params=sampler.next()

	loss=score_loss(params)

	if loss>=0:
		sampler.update(token, loss)


if __name__=='__main__':
	hypscan()
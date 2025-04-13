# Installing FEniCSx using docker image.
```
sudo docker run -ti ghcr.io/fenics/dolfinx/dolfinx:v0.6.0
```
install the ambit_fe in the container
```
python3 -m pip install ambit-fe
```
If exited from the container
clone the ambit in the home
```
https://github.com/marchirschvogel/ambit.git
```
run the container
```
sudo docker run -t mount src=path,target=home/shared,type=bind, 
```
testing the installation
```
cd /home/shared/test/
./runtests.py
```
# Installing FEniCSx using singularity from docker image.
## Using the docker image of ambit provided  by Marc
```
singularity  pull ambit_fe.img docker://ghcr.io/marchirschvogel/ambit:latest
```

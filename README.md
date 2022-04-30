# MLSimulation

This repository is a codebase of a thesis project "Simulation of a Seller-Customer Type Service and Modelling the Optimal Agents' Strategies With Reinforcement Learning Algorithms". 

The root directory, it contains the tuned code for the baseline of the recommender system model based on https://www.kaggle.com/code/hariwh0/userbehavior-ecommerce-transformers4rec. 

/TheSimulator directory contains two subdirectories:
* /Prefereces has the code used to generate user preferences and item-price tuples for the simulation of the user behavior. The data used in the code can be found at https://www.kaggle.com/datasets/mkechinov/ecommerce-events-history-in-cosmetics-shop.
* /MarketplaceSim contains the C++ code for the simulator, designed to provide the revenue approximation after having taken user preferences, item-price tuples and model recommendations as input. It supports loading changed recommendations at runtime, allowing seamless interaction between it and models like Reinforcement Learning. It contains sample input files due to the file size limitations, however, the original input files can be reproduced using the code from /Preferences and the data specified above.
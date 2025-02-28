from copy import deepcopy
from scipy.spatial.distance import cdist
from scipy.sparse import spdiags, csr_matrix, diags
from scipy.linalg import inv, sqrtm
from scipy.sparse.linalg import inv

import numpy as np
import mne
from scipy.fftpack import dct
from scipy.linalg import toeplitz
import matplotlib.pyplot as plt

from ..util import pos_from_forward
# from ..invert import BaseSolver, InverseOperator
# from .. import invert
from .base import BaseSolver, InverseOperator
from time import time
# from .. import invert
# import BaseSolver, InverseOperator


class SolverChampagneOld(BaseSolver):
    ''' Class for the Champagne inverse solution. Code is based on the
    implementation from the BSI-Zoo: https://github.com/braindatalab/BSI-Zoo/
    
    References
    ----------
    [1] Owen, J., Attias, H., Sekihara, K., Nagarajan, S., & Wipf, D. (2008).
    Estimating the location and orientation of complex, correlated neural
    activity using MEG. Advances in Neural Information Processing Systems, 21.
    
    [2] Wipf, D. P., Owen, J. P., Attias, H. T., Sekihara, K., & Nagarajan, S.
    S. (2010). Robust Bayesian estimation of the location, orientation, and time
    course of multiple correlated neural sources using MEG. NeuroImage, 49(1),
    641-655. 
    
    [3] Owen, J. P., Wipf, D. P., Attias, H. T., Sekihara, K., &
    Nagarajan, S. S. (2012). Performance evaluation of the Champagne source
    reconstruction algorithm on simulated and real M/EEG data. Neuroimage,
    60(1), 305-323.
    '''

    def __init__(self, name="Champagne", **kwargs):
        self.name = name
        return super().__init__(**kwargs)

    def make_inverse_operator(self, forward, mne_obj, *args, alpha='auto', max_iter=1000, noise_cov=None, verbose=0, **kwargs):
        ''' Calculate inverse operator.

        Parameters
        ----------
        forward : mne.Forward
            The mne-python Forward model instance.
        mne_obj : [mne.Evoked, mne.Epochs, mne.io.Raw]
            The MNE data object.
        alpha : float
            The regularization parameter.
        max_iter : int
            Maximum number of iterations.
        noise_cov : [None, numpy.ndarray]
            The noise covariance matrix. Use "None" if not available.
        
        Return
        ------
        self : object returns itself for convenience

        '''
        super().make_inverse_operator(forward, *args, alpha=alpha, **kwargs)
        data = self.unpack_data_obj(mne_obj)

        n_chans = self.leadfield.shape[0]
        if noise_cov is None:
            noise_cov = np.identity(n_chans)
        self.noise_cov = noise_cov
        
        inverse_operators = []
        for alpha in self.alphas:
            inverse_operator = self.champagne(data, alpha, max_iter=max_iter,)
            inverse_operators.append( inverse_operator )
        self.inverse_operators = [InverseOperator(inverse_operator, self.name) for inverse_operator in inverse_operators]
        return self
    
    def champagne(self, y, alpha, max_iter=1000):
        ''' Champagne method.

        Parameters
        ----------
        y : array, shape (n_sensors,)
            measurement vector, capturing sensor measurements
        alpha : float
            The regularization parameter.
        max_iter : int, optional
            The maximum number of inner loop iterations

        Returns
        -------
        x : array, shape (dipoles, time)
            Parameter vector, e.g., source vector in the context of BSI (x in the cost
            function formula).
        
        '''
        n_chans, n_sources = self.leadfield.shape
        _, n_times = y.shape
        
        y -= y.mean(axis=0)
        scaler = abs(y).mean()#np.mean(np.diag(y@y.T/n_times))
        
        y_scaled = y / scaler
        leadfield = deepcopy(self.leadfield)
        n_active = n_sources
        active_set = np.arange(n_sources)
        gammas = np.ones(n_sources)
        # eps = np.finfo(float).eps
        I = np.identity(n_chans)
        noise_cov = I*(alpha**2)
        threshold = 1e-3#0.2 * np.mean(np.diag(noise_cov))
        
        x_bars = []
        for i in range(max_iter):
            gammas[np.isnan(gammas)] = 0.0
            gidx = np.abs(gammas) > threshold
            active_set = active_set[gidx]
            gammas = gammas[gidx]

            # update only active gammas (once set to zero it stays at zero)
            if n_active > len(active_set):
                n_active = active_set.size
                leadfield = leadfield[:, gidx]

            Gamma = spdiags(gammas, 0, len(active_set), len(active_set))
            # Calculate Source Covariance Matrix based on currently selected gammas
            Sigma_y = (leadfield @ Gamma @ leadfield.T) + noise_cov
            # Sigma_y_inv = self.alt_inv(Sigma_y, eps)
            Sigma_y_inv = np.linalg.inv(Sigma_y)
            
            # Sigma_y_inv = linalg.inv(Sigma_y)
            x_bar = Gamma @ leadfield.T @ Sigma_y_inv @ y_scaled

            # old gamma calculation throws warning
            gammas = np.sqrt(
                np.diag(x_bar @ x_bar.T / n_times) / np.diag(leadfield.T @ Sigma_y_inv @ leadfield)
            )

            # Calculate Residual to the data
            # e_bar = y_scaled - (leadfield @ x_bar)
            # noise_cov = np.sqrt(np.diag(e_bar @ e_bar.T / n_times) / np.diag(Sigma_y_inv))
            # threshold = 0.2 * np.mean(np.diag(noise_cov))
            x_bars.append(x_bar)

            if i>0 and np.linalg.norm(x_bars[-1]) == 0:
                x_bar = x_bars[-2]
                break
        # active_set
        gammas_full = np.zeros(n_sources)
        gammas_full[active_set] = gammas
        Gamma_full = spdiags(gammas_full, 0, n_sources, n_sources)
        Sigma_y = (self.leadfield @ Gamma_full @ self.leadfield.T) + noise_cov*scaler #I*scaler
        # Sigma_y_inv = self.alt_inv(Sigma_y, eps)
        Sigma_y_inv = np.linalg.inv(Sigma_y)
            


        inverse_operator = Gamma_full @ self.leadfield.T @ Sigma_y_inv

        return inverse_operator

    def alt_inv(self, Sigma_y, eps):
        U, S, _ = np.linalg.svd(Sigma_y, full_matrices=False)
        S = S[np.newaxis, :]
        del Sigma_y
        Sigma_y_inv = np.dot(U / (S + eps), U.T)
        return Sigma_y_inv

class SolverEMChampagne(BaseSolver):
    ''' Class for the Expectation Maximization Champagne inverse solution. 

    References
    ----------
    [1] Hashemi, A., & Haufe, S. (2018, September). Improving EEG source
    localization through spatio-temporal sparse Bayesian learning. In 2018 26th
    European Signal Processing Conference (EUSIPCO) (pp. 1935-1939). IEEE.
    '''

    def __init__(self, name="EM-Champagne", **kwargs):
        self.name = name
        return super().__init__(**kwargs)

    def make_inverse_operator(self, forward, mne_obj, *args, alpha='auto', 
                              max_iter=1000, noise_cov=None, prune=True, 
                              pruning_thresh=1e-3, convergence_criterion=1e-8, 
                              **kwargs):
        ''' Calculate inverse operator.

        Parameters
        ----------
        forward : mne.Forward
            The mne-python Forward model instance.
        mne_obj : [mne.Evoked, mne.Epochs, mne.io.Raw]
            The MNE data object.
        alpha : float
            The regularization parameter.
        max_iter : int
            Maximum number of iterations.
        noise_cov : [None, numpy.ndarray]
            The noise covariance matrix. Use "None" if not available.
        prune : bool
            If True, the algorithm sets small-activity dipoles to zero
            (pruning).
        pruning_thresh : float
            The threshold at which small gammas (dipole candidates) are set to
            zero.
        convergence_criterion : float
            Minimum change of loss function until convergence is assumed.
        
        Return
        ------
        self : object returns itself for convenience

        '''
        super().make_inverse_operator(forward, *args, alpha=alpha, **kwargs)
        data = self.unpack_data_obj(mne_obj)

        n_chans = self.leadfield.shape[0]
        if noise_cov is None:
            noise_cov = np.identity(n_chans)
        self.noise_cov = noise_cov
        self.get_alphas(reference=self.leadfield@self.leadfield.T)
        inverse_operators = []
        for alpha in self.alphas:
            inverse_operator = self.em_champagne(data, alpha, max_iter=max_iter, prune=prune, pruning_thresh=pruning_thresh, convergence_criterion=convergence_criterion)
            inverse_operators.append( inverse_operator )
        self.inverse_operators = [InverseOperator(inverse_operator, self.name) for inverse_operator in inverse_operators]
        return self
    
    def em_champagne(self, Y, alpha, max_iter=1000, prune=True, 
                          pruning_thresh=1e-3, convergence_criterion=1e-8):
        ''' Expectation Maximization Champagne method.

        Parameters
        ----------
        Y : array, shape (n_sensors,)
            measurement vector, capturing sensor measurements
        alpha : float
            The regularization parameter.
        max_iter : int, optional
            The maximum number of inner loop iterations
        prune : bool
            If True, the algorithm sets small-activity dipoles to zero (pruning).
        pruning_thresh : float
            The threshold at which small gammas (dipole candidates) are set to
            zero.
        convergence_criterion : float
            Minimum change of loss function until convergence is assumed.
        Returns
        -------
        x : numpy.ndarray
            Parameter vector, e.g., source vector in the context of BSI (x in the cost
            function formula).
        
        '''
        n_chans, n_dipoles = self.leadfield.shape
        _, n_times = Y.shape
        L = deepcopy(self.leadfield)
        
        # re-reference data
        Y -= Y.mean(axis=0)

        # Scaling of the data (necessary for convergence criterion and pruning
        # threshold)
        Y_scaled = deepcopy(Y)
        Y_scaled /= abs(Y_scaled).mean()

        I = np.identity(n_chans)
        gammas = np.ones(n_dipoles)
        Gamma = csr_matrix(np.diag(gammas))
        Sigma_y = (alpha**2) * I + L @ Gamma @ L.T
        Sigma_y_inv = np.linalg.inv(Sigma_y)
        Sigma_x = Gamma - Gamma @ L.T @ Sigma_y_inv @ L @ Gamma
        mu_x = Gamma @ L.T @ Sigma_y_inv @ Y_scaled
        loss_list = [1e99,]
        for i in range(max_iter):
            old_gammas = deepcopy(gammas)

            gammas = np.diag(Sigma_x) +  np.mean(mu_x**2, axis=1)
            gammas[np.isnan(gammas)] = 0

            if prune:
                prune_candidates = gammas<pruning_thresh
                gammas[prune_candidates] = 0
                # print("Pruned: ", prune_candidates.sum())
            
            # update rest
            Gamma = csr_matrix(np.diag(gammas))
            Sigma_y = (alpha**2) * I + L @ Gamma @ L.T
            Sigma_y_inv = np.linalg.inv(Sigma_y)
            Sigma_x = Gamma - Gamma @ L.T @ Sigma_y_inv @ L @ Gamma
            mu_x = Gamma @ L.T @ Sigma_y_inv @ Y_scaled
            loss = np.trace(L@Gamma@L.T) + (1/n_times) * (Y_scaled.T@Sigma_y@Y_scaled).sum()
            loss_list.append(loss)

            # Check if gammas went to zero
            if np.linalg.norm(gammas) == 0:
                # print("breaking")
                gammas = old_gammas
                break
            # Check convergence:
            change = loss_list[-2] - loss_list[-1] 
            # print(change)
            if change < convergence_criterion:
                # print("Converged!")
                break
            
        # update rest
        gammas /= gammas.max()
        Gamma = np.diag(gammas)
        Sigma_y = (alpha**2) * I + L @ Gamma @ L.T
        Sigma_y_inv = np.linalg.inv(Sigma_y)
        inverse_operator = Gamma @ L.T @ Sigma_y_inv
        
        # This is how the final source estimate could be calculated:
        # mu_x = inverse_operator @ Y

        return inverse_operator

class SolverMMChampagne(BaseSolver):
    ''' Class for the Majorization Maximization Champagne inverse solution. 

    References
    ----------
    [1] Hashemi, A., & Haufe, S. (2018, September). Improving EEG source
    localization through spatio-temporal sparse Bayesian learning. In 2018 26th
    European Signal Processing Conference (EUSIPCO) (pp. 1935-1939). IEEE.
    '''

    def __init__(self, name="MM-Champagne", **kwargs):
        self.name = name
        return super().__init__(**kwargs)

    def make_inverse_operator(self, forward, mne_obj, *args, alpha='auto', 
                              max_iter=1000, noise_cov=None, prune=True, 
                              pruning_thresh=1e-3, convergence_criterion=1e-8, 
                              **kwargs):
        ''' Calculate inverse operator.

        Parameters
        ----------
        forward : mne.Forward
            The mne-python Forward model instance.
        mne_obj : [mne.Evoked, mne.Epochs, mne.io.Raw]
            The MNE data object.
        alpha : float
            The regularization parameter.
        max_iter : int
            Maximum number of iterations.
        noise_cov : [None, numpy.ndarray]
            The noise covariance matrix. Use "None" if not available.
        prune : bool
            If True, the algorithm sets small-activity dipoles to zero
            (pruning).
        pruning_thresh : float
            The threshold at which small gammas (dipole candidates) are set to
            zero.
        convergence_criterion : float
            Minimum change of loss function until convergence is assumed.
        
        Return
        ------
        self : object returns itself for convenience

        '''
        super().make_inverse_operator(forward, *args, alpha=alpha, **kwargs)
        data = self.unpack_data_obj(mne_obj)

        n_chans = self.leadfield.shape[0]
        if noise_cov is None:
            noise_cov = np.identity(n_chans)
        self.noise_cov = noise_cov
        self.get_alphas(reference=self.leadfield@self.leadfield.T)
        inverse_operators = []
        for alpha in self.alphas:
            inverse_operator = self.mm_champagne(data, alpha, max_iter=max_iter, prune=prune, pruning_thresh=pruning_thresh, convergence_criterion=convergence_criterion)
            inverse_operators.append( inverse_operator )
        self.inverse_operators = [InverseOperator(inverse_operator, self.name) for inverse_operator in inverse_operators]
        return self
    
    def mm_champagne(self, Y, alpha, max_iter=1000, prune=True, 
                          pruning_thresh=1e-3, convergence_criterion=1e-8):
        ''' Majority Maximization Champagne method.

        Parameters
        ----------
        Y : array, shape (n_sensors,)
            measurement vector, capturing sensor measurements
        alpha : float
            The regularization parameter.
        max_iter : int, optional
            The maximum number of inner loop iterations
        prune : bool
            If True, the algorithm sets small-activity dipoles to zero (pruning).
        pruning_thresh : float
            The threshold at which small gammas (dipole candidates) are set to
            zero.
        convergence_criterion : float
            Minimum change of loss function until convergence is assumed.
        Returns
        -------
        x : numpy.ndarray
            Parameter vector, e.g., source vector in the context of BSI (x in the cost
            function formula).
        
        '''
        n_chans, n_dipoles = self.leadfield.shape
        _, n_times = Y.shape
        L = deepcopy(self.leadfield)
        
        # re-reference data
        Y -= Y.mean(axis=0)

        # Scaling of the data (necessary for convergence criterion and pruning
        # threshold)
        Y_scaled = deepcopy(Y)
        Y_scaled /= abs(Y_scaled).mean()

        I = np.identity(n_chans)
        gammas = np.ones(n_dipoles)
        Gamma = np.diag(gammas)
        Sigma_y = (alpha**2) * I + L @ Gamma @ L.T
        Sigma_y_inv = np.linalg.inv(Sigma_y)
        # Sigma_x = Gamma - Gamma @ L.T @ Sigma_y_inv @ L @ Gamma
        # z_0 = L.T @ Sigma_y_inv @ L
        mu_x = Gamma @ L.T @ Sigma_y_inv @ Y_scaled
        loss_list = [1e99,]
        
        for i in range(max_iter):
            old_gammas = deepcopy(gammas)
            
            gammas = np.sqrt(np.mean(mu_x**2, axis=1) / np.diag(L.T @ Sigma_y_inv @ L))
            
            gammas[np.isnan(gammas)] = 0
            # print("max gamma: ", gammas.max())
            if prune:
                prune_candidates = gammas<pruning_thresh
                gammas[prune_candidates] = 0
            
            # update rest
            Gamma = np.diag(gammas)
            Sigma_y = (alpha**2) * I + L @ Gamma @ L.T
            Sigma_y_inv = np.linalg.inv(Sigma_y)
            # Sigma_x = Gamma - Gamma @ L.T @ Sigma_y_inv @ L @ Gamma
            mu_x = Gamma @ L.T @ Sigma_y_inv @ Y_scaled
            loss = np.trace(L@Gamma@L.T) + (1/n_times) * (Y_scaled.T@Sigma_y@Y_scaled).sum()
            # first_term = z.T @ gammas
            # loss = first_term + second_term
            loss_list.append(loss)

            # Check if gammas went to zero
            if np.linalg.norm(gammas) == 0:
                # print("breaking")
                gammas = old_gammas
                break
            # Check convergence:
            change = loss_list[-2] - loss_list[-1] 
            # print(change)
            if change < convergence_criterion:
                # print("Converged!")
                break
        
        # update rest
        gammas /= gammas.max()
        Gamma = np.diag(gammas)
        Sigma_y = (alpha**2) * I + L @ Gamma @ L.T
        Sigma_y_inv = np.linalg.inv(Sigma_y)
        inverse_operator = Gamma @ L.T @ Sigma_y_inv
        
        return inverse_operator

class SolverMacKayChampagne(BaseSolver):
    ''' Class for the MacKay Champagne (MacKay) inverse solution. 

    References
    ----------
    [1] Cai, C., Kang, H., Hashemi, A., Chen, D., Diwakar, M., Haufe, S., ... &
    Nagarajan, S. S. (2022). Bayesian algorithms for joint estimation of brain
    activity and noise in electromagnetic imaging. IEEE Transactions on Medical
    Imaging.
    '''

    def __init__(self, name="MacKay-Champagne", **kwargs):
        self.name = name
        return super().__init__(**kwargs)

    def make_inverse_operator(self, forward, mne_obj, *args, alpha='auto', 
                              max_iter=1000, noise_cov=None, prune=True, 
                              pruning_thresh=1e-3, convergence_criterion=1e-8, 
                              **kwargs):
        ''' Calculate inverse operator.

        Parameters
        ----------
        forward : mne.Forward
            The mne-python Forward model instance.
        mne_obj : [mne.Evoked, mne.Epochs, mne.io.Raw]
            The MNE data object.
        alpha : float
            The regularization parameter.
        max_iter : int
            Maximum number of iterations.
        noise_cov : [None, numpy.ndarray]
            The noise covariance matrix. Use "None" if not available.
        prune : bool
            If True, the algorithm sets small-activity dipoles to zero
            (pruning).
        pruning_thresh : float
            The threshold at which small gammas (dipole candidates) are set to
            zero.
        convergence_criterion : float
            Minimum change of loss function until convergence is assumed.
        
        Return
        ------
        self : object returns itself for convenience

        '''
        super().make_inverse_operator(forward, *args, alpha=alpha, **kwargs)
        data = self.unpack_data_obj(mne_obj)

        n_chans = self.leadfield.shape[0]
        if noise_cov is None:
            noise_cov = np.identity(n_chans)
        self.noise_cov = noise_cov
        self.get_alphas(reference=self.leadfield@self.leadfield.T)
        inverse_operators = []
        for alpha in self.alphas:
            inverse_operator = self.mackay_champagne(data, alpha, max_iter=max_iter, prune=prune, pruning_thresh=pruning_thresh, convergence_criterion=convergence_criterion)
            inverse_operators.append( inverse_operator )
        self.inverse_operators = [InverseOperator(inverse_operator, self.name) for inverse_operator in inverse_operators]
        return self
    
    def mackay_champagne(self, Y, alpha, max_iter=1000, prune=True, 
                          pruning_thresh=1e-3, convergence_criterion=1e-8):
        ''' Majority Maximization Champagne method.

        Parameters
        ----------
        Y : array, shape (n_sensors,)
            measurement vector, capturing sensor measurements
        alpha : float
            The regularization parameter.
        max_iter : int, optional
            The maximum number of inner loop iterations
        prune : bool
            If True, the algorithm sets small-activity dipoles to zero (pruning).
        pruning_thresh : float
            The threshold at which small gammas (dipole candidates) are set to
            zero.
        convergence_criterion : float
            Minimum change of loss function until convergence is assumed.
        Returns
        -------
        x : numpy.ndarray
            Parameter vector, e.g., source vector in the context of BSI (x in the cost
            function formula).
        
        '''
        n_chans, n_dipoles = self.leadfield.shape
        _, n_times = Y.shape
        L = deepcopy(self.leadfield)
        
        # re-reference data
        Y -= Y.mean(axis=0)

        # Scaling of the data (necessary for convergence criterion and pruning
        # threshold)
        Y_scaled = deepcopy(Y)
        Y_scaled /= abs(Y_scaled).mean()

        I = np.identity(n_chans)
        gammas = np.ones(n_dipoles)
        Gamma = np.diag(gammas)
        Sigma_y = (alpha**2) * I + L @ Gamma @ L.T
        Sigma_y_inv = np.linalg.inv(Sigma_y)
        # Sigma_x = Gamma - Gamma @ L.T @ Sigma_y_inv @ L @ Gamma
        # z_0 = L.T @ Sigma_y_inv @ L
        mu_x = Gamma @ L.T @ Sigma_y_inv @ Y_scaled
        loss_list = [1e99,]
        for _ in range(max_iter):
            old_gammas = deepcopy(gammas)
            
            # gammas = np.mean(mu_x**2, axis=1) / (gammas * np.diag(L.T @ Sigma_y_inv @ L))
            gammas = np.mean(mu_x**2, axis=1) / (gammas * np.diag(L.T @ Sigma_y_inv @ L) + 1e-8)  # Adding a small constant for numerical stability
            # gammas = np.squeeze(np.mean(mu_x**2, axis=1) / (gammas[np.newaxis, :] @ L.T @ Sigma_y_inv @ L))  # New rule for MacKay
            
            gammas[np.isnan(gammas)] = 0
            
            # print("max gamma: ", gammas.max())
            if prune:
                prune_candidates = gammas<pruning_thresh
                gammas[prune_candidates] = 0
            
            # update rest
            Gamma = np.diag(gammas)
            Sigma_y = (alpha**2) * I + L @ Gamma @ L.T
            Sigma_y_inv = np.linalg.inv(Sigma_y)
            # Sigma_x = Gamma - Gamma @ L.T @ Sigma_y_inv @ L @ Gamma
            mu_x = Gamma @ L.T @ Sigma_y_inv @ Y_scaled
            loss = np.trace(L@Gamma@L.T) + (1/n_times) * (Y_scaled.T@Sigma_y@Y_scaled).sum()

            # # ChatGPT LOSS
            # # Compute the residuals
            residuals = Y_scaled - L @ mu_x

            # # Log likelihood term
            # log_likelihood_data = -0.5 * np.log(np.linalg.det(Sigma_y)) - 0.5 * residuals.T @ Sigma_y_inv @ residuals

            # # Regularization term
            # log_likelihood_prior = -0.5 * np.log(np.linalg.det(Gamma)) - 0.5 * mu_x.T @ np.linalg.inv(Gamma) @ mu_x

            # # Total loss
            # loss = log_likelihood_data + alpha * log_likelihood_prior


            loss_list.append(loss)

            # Check if gammas went to zero
            if np.linalg.norm(gammas) == 0:
                # print("breaking")
                gammas = old_gammas
                break
            # Check convergence:
            change = loss_list[-2] - loss_list[-1] 
            print(f"\tloss: {loss:.1f}")
            print(f"\tchange: {change:.1f} ")
            print(f"\tresiduals: {np.linalg.norm(residuals):.3f}")
            if change < convergence_criterion:
                # print("Converged!")
                break
            
        # update rest
        # gammas /= gammas.max()
        Gamma = np.diag(gammas)
        Sigma_y = (alpha**2) * I + L @ Gamma @ L.T
        Sigma_y_inv = np.linalg.inv(Sigma_y)
        inverse_operator = Gamma @ L.T @ Sigma_y_inv
        
        # This is how the final source estimate could be calculated:
        # mu_x = inverse_operator @ Y


        return inverse_operator

class SolverConvexityChampagne(BaseSolver):
    ''' Class for the Convexity Champagne inverse solution. 

    References
    ----------
    [1] Cai, C., Kang, H., Hashemi, A., Chen, D., Diwakar, M., Haufe, S., ... &
    Nagarajan, S. S. (2022). Bayesian algorithms for joint estimation of brain
    activity and noise in electromagnetic imaging. IEEE Transactions on Medical
    Imaging.
    '''

    def __init__(self, name="Convexity-Champagne", **kwargs):
        self.name = name
        return super().__init__(**kwargs)

    def make_inverse_operator(self, forward, mne_obj, *args, alpha='auto', 
                              max_iter=1000, noise_cov=None, prune=True, 
                              pruning_thresh=1e-3, convergence_criterion=1e-8, 
                              **kwargs):
        ''' Calculate inverse operator.

        Parameters
        ----------
        forward : mne.Forward
            The mne-python Forward model instance.
        mne_obj : [mne.Evoked, mne.Epochs, mne.io.Raw]
            The MNE data object.
        alpha : float
            The regularization parameter.
        max_iter : int
            Maximum number of iterations.
        noise_cov : [None, numpy.ndarray]
            The noise covariance matrix. Use "None" if not available.
        prune : bool
            If True, the algorithm sets small-activity dipoles to zero
            (pruning).
        pruning_thresh : float
            The threshold at which small gammas (dipole candidates) are set to
            zero.
        convergence_criterion : float
            Minimum change of loss function until convergence is assumed.
        
        Return
        ------
        self : object returns itself for convenience

        '''
        super().make_inverse_operator(forward, *args, alpha=alpha, **kwargs)
        data = self.unpack_data_obj(mne_obj)

        n_chans = self.leadfield.shape[0]
        if noise_cov is None:
            noise_cov = np.identity(n_chans)
        self.noise_cov = noise_cov
        self.get_alphas(reference=self.leadfield@self.leadfield.T)
        inverse_operators = []
        for alpha in self.alphas:
            inverse_operator = self.mackay_champagne(data, alpha, max_iter=max_iter, prune=prune, pruning_thresh=pruning_thresh, convergence_criterion=convergence_criterion)
            inverse_operators.append( inverse_operator )
        self.inverse_operators = [InverseOperator(inverse_operator, self.name) for inverse_operator in inverse_operators]
        return self
    
    def mackay_champagne(self, Y, alpha, max_iter=1000, prune=True, 
                          pruning_thresh=1e-3, convergence_criterion=1e-8):
        ''' Majority Maximization Champagne method.

        Parameters
        ----------
        Y : array, shape (n_sensors,)
            measurement vector, capturing sensor measurements
        alpha : float
            The regularization parameter.
        max_iter : int, optional
            The maximum number of inner loop iterations
        prune : bool
            If True, the algorithm sets small-activity dipoles to zero (pruning).
        pruning_thresh : float
            The threshold at which small gammas (dipole candidates) are set to
            zero.
        convergence_criterion : float
            Minimum change of loss function until convergence is assumed.
        Returns
        -------
        x : numpy.ndarray
            Parameter vector, e.g., source vector in the context of BSI (x in the cost
            function formula).
        
        '''
        n_chans, n_dipoles = self.leadfield.shape
        _, n_times = Y.shape
        L = deepcopy(self.leadfield)
        
        # re-reference data
        Y -= Y.mean(axis=0)

        # Scaling of the data (necessary for convergence criterion and pruning
        # threshold)
        Y_scaled = deepcopy(Y)
        Y_scaled /= abs(Y_scaled).mean()

        I = np.identity(n_chans)
        gammas = np.ones(n_dipoles)
        Gamma = np.diag(gammas)
        Sigma_y = (alpha**2) * I + L @ Gamma @ L.T
        Sigma_y_inv = np.linalg.inv(Sigma_y)
        # Sigma_x = Gamma - Gamma @ L.T @ Sigma_y_inv @ L @ Gamma
        # z_0 = L.T @ Sigma_y_inv @ L
        mu_x = Gamma @ L.T @ Sigma_y_inv @ Y_scaled
        
        loss_list = [1e99,]
        for i in range(max_iter):
            old_gammas = deepcopy(gammas)

            gammas = np.sqrt(np.mean(mu_x**2, axis=1) / np.array([np.sum(L[:, jj] * (Sigma_y_inv @ L[:, jj])) for jj in range(L.shape[1])]))
            # # z = []
            # for n in range(len(gammas)):
            #     Ln = L[:, n][:,np.newaxis]
            #     g = np.trace(Ln.T @ Sigma_y_inv @ Ln)
            #     upper_term = (1/n_times) *(mu_x[n]**2).sum()
            #     gammas[n] = np.sqrt(upper_term / g)

            gammas[np.isnan(gammas)] = 0
            # print("max gamma: ", gammas.max())
            if prune:
                prune_candidates = gammas<pruning_thresh
                gammas[prune_candidates] = 0
            
            # update rest
            Gamma = np.diag(gammas)
            Sigma_y = (alpha**2) * I + L @ Gamma @ L.T
            Sigma_y_inv = np.linalg.inv(Sigma_y)
            # Sigma_x = Gamma - Gamma @ L.T @ Sigma_y_inv @ L @ Gamma
            mu_x = Gamma @ L.T @ Sigma_y_inv @ Y_scaled
            loss = np.trace(L@Gamma@L.T) + (1/n_times) * (Y_scaled.T@Sigma_y@Y_scaled).sum()
            # first_term = z.T @ gammas
            # loss = first_term + second_term
            loss_list.append(loss)

            # Check if gammas went to zero
            if np.linalg.norm(gammas) == 0:
                # print("breaking")
                gammas = old_gammas
                break
            # Check convergence:
            change = loss_list[-2] - loss_list[-1] 
            print(f"loss: {loss}")
            # print(change)
            if change < convergence_criterion:
                # print("Converged!")
                break
            
        # update rest
        gammas /= gammas.max()
        Gamma = np.diag(gammas)
        Sigma_y = (alpha**2) * I + L @ Gamma @ L.T
        Sigma_y_inv = np.linalg.inv(Sigma_y)
        inverse_operator = Gamma @ L.T @ Sigma_y_inv
        
        # This is how the final source estimate could be calculated:
        # mu_x = inverse_operator @ Y
        
        return inverse_operator

class  SolverTEMChampagne(BaseSolver):
    ''' Class for the Temporal Expectation Maximization Champagne (T-EM
    Champagne) inverse solution. 

    References
    ----------
    [1] Hashemi, A., & Haufe, S. (2018, September). Improving EEG source
    localization through spatio-temporal sparse Bayesian learning. In 2018 26th
    European Signal Processing Conference (EUSIPCO) (pp. 1935-1939). IEEE.
    '''

    def __init__(self, name="TEM-Champagne", **kwargs):
        self.name = name
        return super().__init__(**kwargs)

    def make_inverse_operator(self, forward, mne_obj, *args, alpha='auto', 
                              max_iter=1000, noise_cov=None, prune=True, 
                              pruning_thresh=1e-3, convergence_criterion=1e-8, 
                              theta=0.01, **kwargs):
        ''' Calculate inverse operator.

        Parameters
        ----------
        forward : mne.Forward
            The mne-python Forward model instance.
        mne_obj : [mne.Evoked, mne.Epochs, mne.io.Raw]
            The MNE data object.
        alpha : float
            The regularization parameter.
        max_iter : int
            Maximum number of iterations.
        noise_cov : [None, numpy.ndarray]
            The noise covariance matrix. Use "None" if not available.
        prune : bool
            If True, the algorithm sets small-activity dipoles to zero
            (pruning).
        pruning_thresh : float
            The threshold at which small gammas (dipole candidates) are set to
            zero.
        convergence_criterion : float
            Minimum change of loss function until convergence is assumed.
        theta : float
            Another regularization term
        Return
        ------
        self : object returns itself for convenience

        '''
        super().make_inverse_operator(forward, *args, alpha=alpha, **kwargs)
        data = self.unpack_data_obj(mne_obj)

        n_chans = self.leadfield.shape[0]
        if noise_cov is None:
            noise_cov = np.identity(n_chans)
        self.noise_cov = noise_cov
        self.get_alphas(reference=self.leadfield@self.leadfield.T)
        inverse_operators = []
        for alpha in self.alphas:
            inverse_operator = self.tem_champagne(data, alpha, max_iter=max_iter, prune=prune, pruning_thresh=pruning_thresh, convergence_criterion=convergence_criterion, theta=theta)
            inverse_operators.append( inverse_operator )
        self.inverse_operators = [InverseOperator(inverse_operator, self.name) for inverse_operator in inverse_operators]
        return self

    def tem_champagne(self, Y, alpha, max_iter=1000, prune=True, 
                          pruning_thresh=1e-3, convergence_criterion=1e-8,
                          theta=0.01):
        ''' Temporal Trade-Off Expectation Maximization Champagne method.

        Parameters
        ----------
        Y : array, shape (n_sensors,)
            measurement vector, capturing sensor measurements
        alpha : float
            The regularization parameter.
        max_iter : int, optional
            The maximum number of inner loop iterations
        prune : bool
            If True, the algorithm sets small-activity dipoles to zero (pruning).
        pruning_thresh : float
            The threshold at which small gammas (dipole candidates) are set to
            zero.
        convergence_criterion : float
            Minimum change of loss function until convergence is assumed.
        theta : float
            Another regularization term

        Returns
        -------
        x : numpy.ndarray
            Parameter vector, e.g., source vector in the context of BSI (x in the cost
            function formula).
        
        '''
        n_chans, n_dipoles = self.leadfield.shape
        _, n_times = Y.shape
        L = deepcopy(self.leadfield)
        
        # re-reference data
        Y -= Y.mean(axis=0)

        # Scaling of the data (necessary for convergence criterion and pruning
        # threshold)
        Y_scaled = deepcopy(Y)
        Y_scaled /= abs(Y_scaled).mean()

        I = np.identity(n_chans)
        It = np.identity(n_times)
        gammas = np.ones(n_dipoles)
        Gamma = np.diag(gammas)
        Sigma_y = (alpha**2) * I + L @ Gamma @ L.T
        Sigma_y_inv = np.linalg.inv(Sigma_y)
        Sigma_x = Gamma - Gamma @ L.T @ Sigma_y_inv @ L @ Gamma
        mu_x = Gamma @ L.T @ Sigma_y_inv @ Y_scaled
        B_hat = np.stack([(mu_x[nn, np.newaxis].T * mu_x[nn, np.newaxis]) / gammas[nn] for nn in range(n_dipoles)], axis=0).sum(axis=0) + theta*It
        B = B_hat / self.frob(B_hat)

        loss_list = [1e99,]
        for i in range(max_iter):
            old_gammas = deepcopy(gammas)

            for n in range(len(gammas)):
                mu_x_n = mu_x[n][np.newaxis]
                gammas[n] = Sigma_x[n,n] + mu_x_n @ np.linalg.inv(B) @ mu_x_n.T

            gammas[np.isnan(gammas)] = 0
            # print("max gamma: ", gammas.max())
            if prune:
                prune_candidates = gammas<pruning_thresh
                gammas[prune_candidates] = 0
                print("Pruned: ", prune_candidates.sum())
            # print((gammas==0).sum())
            
            # update rest
            Gamma = np.diag(gammas)
            Sigma_y = (alpha**2) * I + L @ Gamma @ L.T
            Sigma_y_inv = np.linalg.inv(Sigma_y)
            Sigma_x = Gamma - Gamma @ L.T @ Sigma_y_inv @ L @ Gamma
            mu_x = Gamma @ L.T @ Sigma_y_inv @ Y_scaled
            B_hat = np.stack([(mu_x[nn, np.newaxis].T * mu_x[nn, np.newaxis]) / gammas[nn] for nn in range(n_dipoles)], axis=0).sum(axis=0) + theta*It
            B = B_hat / self.frob(B_hat)

            Sigma_0 = np.kron(Gamma, B)
            D = np.kron(L, It)
            Sigma_y_temp = alpha * It + D@Sigma_0@D.T
            y_temp = Y_scaled.reshape(n_chans*n_times, 1)
            loss = np.log(np.linalg.norm(Sigma_y_temp)) + y_temp.T @ np.linalg.inv(Sigma_y_temp) @ y_temp
            # loss = np.trace(L@Gamma@L.T) + (1/n_times) * (Y_scaled.T@Sigma_y@Y_scaled).sum()
            loss_list.append(loss)

            # Check if gammas went to zero
            if np.linalg.norm(gammas) == 0:
                # print("breaking")
                gammas = old_gammas
                break
            # Check convergence:
            change = loss_list[-2] - loss_list[-1] 
            print(change)
            # if change < convergence_criterion:
            #     print("Converged!")
            #     break
            
        # update rest
        gammas /= gammas.max()
        Gamma = np.diag(gammas)
        Sigma_y = (alpha**2) * I + L @ Gamma @ L.T
        Sigma_y_inv = np.linalg.inv(Sigma_y)
        inverse_operator = Gamma @ L.T @ Sigma_y_inv
        
        # This is how the final source estimate could be calculated:
        # mu_x = inverse_operator @ Y


        return inverse_operator

    @staticmethod
    def frob(x):
        if len(x.shape) == 1:
            x = x[:, np.newaxis]
        return np.sqrt(np.trace(x@x.T))

class SolverLowSNRChampagne(BaseSolver):
    ''' Class for the LOW SNR Champagne inverse solution. 

    References
    ----------
    [1] Hashemi, A., & Haufe, S. (2018, September). Improving EEG source
    localization through spatio-temporal sparse Bayesian learning. In 2018 26th
    European Signal Processing Conference (EUSIPCO) (pp. 1935-1939). IEEE.
    '''

    def __init__(self, name="LowSNR-Champagne", **kwargs):
        self.name = name
        return super().__init__(**kwargs)

    def make_inverse_operator(self, forward, mne_obj, *args, alpha='auto', 
                              max_iter=1000, noise_cov=None, prune=True, 
                              pruning_thresh=1e-3, convergence_criterion=1e-8, 
                              **kwargs):
        ''' Calculate inverse operator.

        Parameters
        ----------
        forward : mne.Forward
            The mne-python Forward model instance.
        mne_obj : [mne.Evoked, mne.Epochs, mne.io.Raw]
            The MNE data object.
        alpha : float
            The regularization parameter.
        max_iter : int
            Maximum number of iterations.
        noise_cov : [None, numpy.ndarray]
            The noise covariance matrix. Use "None" if not available.
        prune : bool
            If True, the algorithm sets small-activity dipoles to zero
            (pruning).
        pruning_thresh : float
            The threshold at which small gammas (dipole candidates) are set to
            zero.
        convergence_criterion : float
            Minimum change of loss function until convergence is assumed.
        
        Return
        ------
        self : object returns itself for convenience

        '''
        super().make_inverse_operator(forward, *args, alpha=alpha, **kwargs)
        data = self.unpack_data_obj(mne_obj)

        n_chans = self.leadfield.shape[0]
        if noise_cov is None:
            noise_cov = np.identity(n_chans)
        self.noise_cov = noise_cov
        self.get_alphas(reference=self.leadfield@self.leadfield.T)
        inverse_operators = []
        for alpha in self.alphas:
            inverse_operator = self.low_snr_champagne(data, alpha, max_iter=max_iter, prune=prune, pruning_thresh=pruning_thresh, convergence_criterion=convergence_criterion)
            inverse_operators.append( inverse_operator )
        self.inverse_operators = [InverseOperator(inverse_operator, self.name) for inverse_operator in inverse_operators]
        return self
    
    def low_snr_champagne(self, Y, alpha, max_iter=1000, prune=True, 
                          pruning_thresh=1e-3, convergence_criterion=1e-8):
        ''' Low SNR Champagne method.

        Parameters
        ----------
        Y : array, shape (n_sensors,)
            measurement vector, capturing sensor measurements
        alpha : float
            The regularization parameter.
        max_iter : int, optional
            The maximum number of inner loop iterations
        prune : bool
            If True, the algorithm sets small-activity dipoles to zero (pruning).
        pruning_thresh : float
            The threshold at which small gammas (dipole candidates) are set to
            zero.
        convergence_criterion : float
            Minimum change of loss function until convergence is assumed.
        Returns
        -------
        x : numpy.ndarray
            Parameter vector, e.g., source vector in the context of BSI (x in the cost
            function formula).
        
        '''
        n_chans, n_dipoles = self.leadfield.shape
        _, n_times = Y.shape
        L = deepcopy(self.leadfield)
        
        # re-reference data
        Y -= Y.mean(axis=0)

        # Scaling of the data (necessary for convergence criterion and pruning
        # threshold)
        Y_scaled = deepcopy(Y)
        Y_scaled /= abs(Y_scaled).mean()

        I = np.identity(n_chans)
        gammas = np.ones(n_dipoles)
        Gamma = np.diag(gammas)
        Sigma_y = (alpha**2) * I + L @ Gamma @ L.T
        Sigma_y_inv = np.linalg.inv(Sigma_y)
        # Sigma_x = Gamma - Gamma @ L.T @ Sigma_y_inv @ L @ Gamma
        mu_x = Gamma @ L.T @ Sigma_y_inv @ Y_scaled
        loss_list = [1e99,]
        for i in range(max_iter):
            old_gammas = deepcopy(gammas)

            for n in range(len(gammas)):
                ll = L[:, n][:, np.newaxis]
                LTL = np.diagonal(ll.T@ll)
                gammas[n] = np.sqrt((((mu_x[n]**2).sum()) / n_times) / LTL)

            gammas[np.isnan(gammas)] = 0
            # print("max gamma: ", gammas.max())
            if prune:
                prune_candidates = gammas<pruning_thresh
                gammas[prune_candidates] = 0
                # print("Pruned: ", prune_candidates.sum())
            # print((gammas==0).sum())
            
            # update rest
            Gamma = np.diag(gammas)
            Sigma_y = (alpha**2) * I + L @ Gamma @ L.T
            Sigma_y_inv = np.linalg.inv(Sigma_y)
            mu_x = Gamma @ L.T @ Sigma_y_inv @ Y_scaled
            loss = np.trace(L@Gamma@L.T) + (1/n_times) * (Y_scaled.T@Sigma_y@Y_scaled).sum()
            loss_list.append(loss)

            # Check if gammas went to zero
            if np.linalg.norm(gammas) == 0:
                # print("breaking")
                gammas = old_gammas
                break
            # Check convergence:
            change = loss_list[-2] - loss_list[-1] 
            if change < convergence_criterion:
                # print("Converged!")
                break
            
        # update rest
        gammas /= gammas.max()
        Gamma = np.diag(gammas)
        Sigma_y = (alpha**2) * I + L @ Gamma @ L.T
        Sigma_y_inv = np.linalg.inv(Sigma_y)
        inverse_operator = Gamma @ L.T @ Sigma_y_inv
        
        # This is how the final source estimate could be calculated:
        # mu_x = inverse_operator @ Y


        return inverse_operator

class SolverChampagne(BaseSolver):
    ''' Class for Champagne inverse solution (MacKay, Convexity Bound, LowSNR). 

    References
    ----------
    [1] Cai, C., Kang, H., Hashemi, A., Chen, D., Diwakar, M., Haufe, S., ... &
    Nagarajan, S. S. (2022). Bayesian algorithms for joint estimation of brain
    activity and noise in electromagnetic imaging. IEEE Transactions on Medical
    Imaging.
    '''

    def __init__(self, name="Champagne", update_rule="MacKay", **kwargs):
        '''
        Parameters
        ----------
        update_rule : str
            Either of: "MacKay", "Convexity", "LowSNR"
        '''
        self.name = update_rule + " " + name
        self.update_rule = update_rule
        return super().__init__(**kwargs)

    def make_inverse_operator(self, forward, mne_obj, *args, alpha='auto', 
                              max_iter=1000, noise_cov=None, prune=True, 
                              pruning_thresh=1e-3, convergence_criterion=1e-8, 
                              **kwargs):
        ''' Calculate inverse operator.

        Parameters
        ----------
        forward : mne.Forward
            The mne-python Forward model instance.
        mne_obj : [mne.Evoked, mne.Epochs, mne.io.Raw]
            The MNE data object.
        
        alpha : float
            The regularization parameter.
        max_iter : int
            Maximum number of iterations.
        noise_cov : [None, numpy.ndarray]
            The noise covariance matrix. Use "None" if not available.
        prune : bool
            If True, the algorithm sets small-activity dipoles to zero
            (pruning).
        pruning_thresh : float
            The threshold at which small gammas (dipole candidates) are set to
            zero.
        convergence_criterion : float
            Minimum change of loss function until convergence is assumed.
        
        Return
        ------
        self : object returns itself for convenience

        '''
        super().make_inverse_operator(forward, *args, alpha=alpha, **kwargs)

        # Store attributes
        n_chans = self.leadfield.shape[0]
        if noise_cov is None:
            noise_cov = np.identity(n_chans)
        self.noise_cov = noise_cov
        
        self.max_iter = max_iter
        self.prune = prune
        self.pruning_thresh = pruning_thresh
        self.convergence_criterion = convergence_criterion

        data = self.unpack_data_obj(mne_obj)

        self.get_alphas(reference=self.leadfield@self.leadfield.T)
        inverse_operators = []
        for alpha in self.alphas:
            inverse_operator = self.make_champagne(data, alpha, pruning_thresh=pruning_thresh)
            inverse_operators.append( inverse_operator )
        self.inverse_operators = [InverseOperator(inverse_operator, self.name) for inverse_operator in inverse_operators]
        return self
    def make_champagne(self, Y, alpha, pruning_thresh=1e-3):
        ''' Majority Maximization Champagne method.

        Parameters
        ----------
        Y : array, shape (n_sensors,)
            measurement vector, capturing sensor measurements
        alpha : float
            The regularization parameter.
    
        Returns
        -------
        x : numpy.ndarray
            Parameter vector, e.g., source vector in the context of BSI (x in the cost
            function formula).
        '''
        n_chans, n_dipoles = self.leadfield.shape
        _, n_times = Y.shape
        L = deepcopy(self.leadfield)
        # L /= np.linalg.norm(L, axis=0)
        

        # re-reference data
        # Y -= Y.mean(axis=0)

        # Scaling of the data (necessary for convergence criterion and pruning
        # threshold)
        Y_scaled = deepcopy(Y)
        Y_scaled /= abs(Y_scaled).mean()
        # Y_scaled /= np.linalg.norm(Y_scaled)

        I = np.identity(n_chans)
        gammas = np.ones(n_dipoles)
        Gamma = diags(gammas,0)
        noise_cov = alpha * I
        Sigma_y = noise_cov + L @ Gamma @ L.T
        Sigma_y_inv = np.linalg.inv(Sigma_y)
        # Sigma_x = Gamma - Gamma @ L.T @ Sigma_y_inv @ L @ Gamma
        # z_0 = L.T @ Sigma_y_inv @ L
        mu_x = Gamma @ L.T @ Sigma_y_inv @ Y_scaled
        loss_list = [1e99,]
        active_set = np.arange(n_dipoles)

        for i_iter in range(self.max_iter):
            old_gammas = deepcopy(gammas)
            # Update gammas:
            # if self.update_rule.lower() == "default":
            #     upper_term = np.mean(mu_x**2, axis=1)
            #     lower_term = np.array([np.sum(L[:, jj] * (Sigma_y_inv @ L[:, jj])) for jj in range(L.shape[1])])
            #     gammas = np.sqrt(upper_term / lower_term)

            if self.update_rule.lower() == "mackay":
                upper_term = np.mean(mu_x**2, axis=1)
                lower_term = gammas * np.array([np.sum(L[:, jj] * (Sigma_y_inv @ L[:, jj])) for jj in range(L.shape[1])])
                gammas = upper_term / lower_term

            elif self.update_rule.lower() == "convexity":
                upper_term = np.mean(mu_x**2, axis=1)
                lower_term = np.array([np.sum(L[:, jj] * (Sigma_y_inv @ L[:, jj])) for jj in range(L.shape[1])])
                gammas = np.sqrt(upper_term / lower_term)

            elif self.update_rule.lower() == "lowsnr":
                upper_term = np.mean(mu_x**2, axis=1)
                lower_term = np.sum(L**2, axis=0)
                # gammas = np.sqrt(upper_term / lower_term)
                gammas = np.sqrt(upper_term) / np.sqrt(lower_term)
            elif self.update_rule.lower() == "adaptive":
                # ai-composed update rule
                upper_term = np.mean(mu_x**2, axis=1)
                lower_term = np.array([np.sum(L[:, jj] * (Sigma_y_inv @ L[:, jj])) for jj in range(L.shape[1])])
                snr_estimate = upper_term / np.mean(np.diag(noise_cov))
                
                # Adaptive exponent based on estimated SNR
                adaptive_exponent = 0.5 + 0.5 / (1 + np.exp(-snr_estimate + 5))
                
                # Combine aspects of MacKay, Convexity, and LowSNR rules
                gammas = (upper_term / lower_term) ** adaptive_exponent
            elif self.update_rule.lower() == "dynamic_adaptive":
                upper_term = np.mean(mu_x**2, axis=1)
                lower_term = np.array([np.sum(L[:, jj] * (Sigma_y_inv @ L[:, jj])) for jj in range(L.shape[1])])
                snr_estimate = upper_term / np.mean(np.diag(noise_cov))
                
                # Dynamic scaling factor based on iteration number and SNR
                iteration_factor = 1 - np.exp(-i_iter / 10)  # Assumes 'i' is the current iteration number
                snr_factor = 1 / (1 + np.exp(-snr_estimate + 5))
                
                # Combine MacKay and Convexity rules with dynamic weighting
                mackay_update = upper_term / (gammas * lower_term)
                convexity_update = np.sqrt(upper_term / lower_term)
                
                # Apply dynamic weighting
                weighted_update = (snr_factor * mackay_update + (1 - snr_factor) * convexity_update) ** iteration_factor
                
                # Apply adaptive smoothing
                smoothing_factor = 0.1 * (1 - iteration_factor)
                gammas = (1 - smoothing_factor) * weighted_update + smoothing_factor * gammas
                
                # Apply soft thresholding for sparsity
                # threshold = np.percentile(gammas, 10)  # Adjust percentile as needed
                # gammas = np.maximum(gammas - threshold, 0)
                
            # Remove nans
            gammas[np.isnan(gammas)] = 0

            # Stop if gammas went to zero
            if np.linalg.norm(gammas) == 0:
                # print("breaking")
                gammas = old_gammas
                break

            if self.prune:
                active_set_idc = np.where(gammas>(pruning_thresh*gammas.max()))[0]
                if len(active_set_idc) == 0:
                    # print("pruned too much")
                    gammas = old_gammas
                    break
                active_set = active_set[active_set_idc]
                # print(f"New set: {len(active_set)}")
                gammas = gammas[active_set_idc]
                L = L[:, active_set_idc]
            
            # update rest
            Gamma = diags(gammas,0)
            Sigma_y = noise_cov + L @ Gamma @ L.T
            Sigma_y_inv = np.linalg.inv(Sigma_y)
            # Sigma_x = Gamma - Gamma @ L.T @ Sigma_y_inv @ L @ Gamma
            mu_x = Gamma @ L.T @ Sigma_y_inv @ Y_scaled

            # First term: L @ Gamma @ L.T
            # L_Gamma = L * Gamma.diagonal()  # Element-wise product
            # term1 = np.sum(L_Gamma * L)

            # # Second term: Y_scaled.T @ Sigma_y_inv @ Y_scaled
            # Y_Sigma = Y_scaled.T * Sigma_y_inv.diagonal()  # Element-wise product
            # term2 = np.mean(np.sum(Y_Sigma * Y_scaled.T, axis=1))

            # loss = term1 + term2
            loss = np.trace(L@Gamma@L.T) + np.mean(np.trace(Y_scaled.T@Sigma_y_inv@Y_scaled))
            
            # Compute the residuals
            loss_list.append(loss)
            
            relative_change = (loss_list[-2] - loss) / loss_list[-2]
            if relative_change < self.convergence_criterion:
                break
            
        
        # update rest
        L = deepcopy(self.leadfield)
        gammas_final = np.zeros(n_dipoles)
        gammas_final[active_set] = gammas
        Gamma = diags(gammas_final,0)
        Sigma_y = noise_cov + L @ Gamma @ L.T
        Sigma_y_inv = np.linalg.inv(Sigma_y)
        inverse_operator = Gamma @ L.T @ Sigma_y_inv
        
        # This is how the final source estimate could be calculated:
        # mu_x = inverse_operator @ Y


        return inverse_operator
    
    # def make_champagne(self, Y, alpha):
    #     ''' Majority Maximization Champagne method.

    #     Parameters
    #     ----------
    #     Y : array, shape (n_sensors,)
    #         measurement vector, capturing sensor measurements
    #     alpha : float
    #         The regularization parameter.
    
    #     Returns
    #     -------
    #     x : numpy.ndarray
    #         Parameter vector, e.g., source vector in the context of BSI (x in the cost
    #         function formula).
    #     '''
    #     n_chans, n_dipoles = self.leadfield.shape
    #     _, n_times = Y.shape
    #     L = deepcopy(self.leadfield)
    #     set_threshold = 1e-3

    #     # re-reference data
    #     Y -= Y.mean(axis=0)

    #     # Scaling of the data (necessary for convergence criterion and pruning
    #     # threshold)
    #     Y_scaled = deepcopy(Y)
    #     Y_scaled /= abs(Y_scaled).mean()

    #     I = np.identity(n_chans)
    #     gammas = np.ones(n_dipoles)
    #     Gamma = diags(gammas,0)
    #     noise_cov = (alpha**2) * I
    #     Sigma_y = noise_cov + L @ Gamma @ L.T
    #     Sigma_y_inv = np.linalg.inv(Sigma_y)
    #     # Sigma_x = Gamma - Gamma @ L.T @ Sigma_y_inv @ L @ Gamma
    #     # z_0 = L.T @ Sigma_y_inv @ L
    #     mu_x = Gamma @ L.T @ Sigma_y_inv @ Y_scaled
    #     loss_list = [1e99,]

    #     for _ in range(self.max_iter):
    #         old_gammas = deepcopy(gammas)
    #         # Update gammas:
    #         # if self.update_rule.lower() == "default":
    #         #     upper_term = np.mean(mu_x**2, axis=1)
    #         #     lower_term = np.array([np.sum(L[:, jj] * (Sigma_y_inv @ L[:, jj])) for jj in range(L.shape[1])])
    #         #     gammas = np.sqrt(upper_term / lower_term)

    #         if self.update_rule.lower() == "mackay":
    #             upper_term = np.mean(mu_x**2, axis=1)
    #             lower_term = gammas * np.array([np.sum(L[:, jj] * (Sigma_y_inv @ L[:, jj])) for jj in range(L.shape[1])])
    #             gammas = upper_term / lower_term

    #         elif self.update_rule.lower() == "convexity":
    #             upper_term = np.mean(mu_x**2, axis=1)
    #             lower_term = np.array([np.sum(L[:, jj] * (Sigma_y_inv @ L[:, jj])) for jj in range(L.shape[1])])
    #             gammas = np.sqrt(upper_term / lower_term)

    #         elif self.update_rule.lower() == "lowsnr":
    #             upper_term = np.mean(mu_x**2, axis=1)
    #             lower_term = np.sum(L**2, axis=0)
    #             # gammas = np.sqrt(upper_term / lower_term)
    #             gammas = np.sqrt(upper_term) / np.sqrt(lower_term)

                
    #         # Remove nans
    #         gammas[np.isnan(gammas)] = 0
            
    #         # Prune low gammas
    #         if self.prune:
    #             prune_candidates = gammas < self.pruning_thresh
    #             gammas[prune_candidates] = 0
            
    #         # update rest
    #         Gamma = diags(gammas,0)
    #         Sigma_y = noise_cov + L @ Gamma @ L.T
    #         Sigma_y_inv = np.linalg.inv(Sigma_y)
    #         # Sigma_x = Gamma - Gamma @ L.T @ Sigma_y_inv @ L @ Gamma
    #         mu_x = Gamma @ L.T @ Sigma_y_inv @ Y_scaled

    #         loss = np.trace(L@Gamma@L.T) + (1/n_times) * (Y_scaled.T@Sigma_y@Y_scaled).sum()
    #         # Compute the residuals
            
    #         loss_list.append(loss)

    #         # Check if gammas went to zero
    #         if np.linalg.norm(gammas) == 0:
    #             # print("breaking")
    #             gammas = old_gammas
    #             break
    #         # Check convergence:
    #         change = loss_list[-2] - loss_list[-1] 
    #         print(gammas.max(), gammas.min(), np.percentile(gammas, 5))
    #         # residuals = Y_scaled - L @ mu_x
    #         # print(f"\tloss: {loss:.1f}")
    #         # print(f"\tchange: {change:.1f} ")
    #         # print(f"\tresiduals: {np.linalg.norm(residuals):.3f}")
    #         print(gammas.max())
    #         if change < self.convergence_criterion:
    #             print("Converged!")
    #             break

            
    #     # update rest
    #     # gammas /= gammas.max()
    #     Gamma = diags(gammas,0)
    #     Sigma_y = noise_cov + L @ Gamma @ L.T
    #     Sigma_y_inv = np.linalg.inv(Sigma_y)
    #     inverse_operator = Gamma @ L.T @ Sigma_y_inv
        
    #     # This is how the final source estimate could be calculated:
    #     # mu_x = inverse_operator @ Y


    #     return inverse_operator

class SolverFUN(BaseSolver):
    ''' Class for the Full-Structure Noise (FUN) inverse solution. 

    References
    ----------
    [1] Hashemi, A., Cai, C., Gao, Y., Ghosh, S., Müller, K. R., Nagarajan, S.
    S., & Haufe, S. (2022). Joint learning of full-structure noise in
    hierarchical Bayesian regression models. IEEE Transactions on Medical
    Imaging.

    '''

    def __init__(self, name="FUN", **kwargs):
        self.name = name
        return super().__init__(**kwargs)

    def make_inverse_operator(self, forward, mne_obj, *args, alpha='auto', 
                              max_iter=1000, noise_cov=None, prune=True, 
                              pruning_thresh=1e-3, convergence_criterion=1e-8, 
                              return_noise_cov=False, **kwargs):
        ''' Calculate inverse operator.

        Parameters
        ----------
        forward : mne.Forward
            The mne-python Forward model instance.
        mne_obj : [mne.Evoked, mne.Epochs, mne.io.Raw]
            The MNE data object.
        alpha : float
            The regularization parameter.
        max_iter : int
            Maximum number of iterations.
        noise_cov : [None, numpy.ndarray]
            The noise covariance matrix. Use "None" if not available.
        prune : bool
            If True, the algorithm sets small-activity dipoles to zero
            (pruning).
        pruning_thresh : float
            The threshold at which small gammas (dipole candidates) are set to
            zero.
        convergence_criterion : float
            Minimum change of loss function until convergence is assumed.
        
        Return
        ------
        self : object returns itself for convenience

        '''
        super().make_inverse_operator(forward, *args, alpha=alpha, **kwargs)
        data = self.unpack_data_obj(mne_obj)

        n_chans = self.leadfield.shape[0]
        if noise_cov is None:
            noise_cov = np.identity(n_chans)
        self.noise_cov = noise_cov
        inverse_operators = []
        inverse_operator, C_noise = self.make_fun(data, max_iter=max_iter, prune=prune, pruning_thresh=pruning_thresh, convergence_criterion=convergence_criterion)
        inverse_operators.append( inverse_operator )
        self.inverse_operators = [InverseOperator(inverse_operator, self.name) for inverse_operator in inverse_operators]
        if return_noise_cov:
            return C_noise
        else:
            return self
    
    def make_fun(self, Y, max_iter=1000, prune=False, 
                pruning_thresh=1e-3, convergence_criterion=1e-8):
        ''' Majority Maximization Champagne method.

        Parameters
        ----------
        Y : array, shape (n_sensors,)
            measurement vector, capturing sensor measurements
        max_iter : int, optional
            The maximum number of inner loop iterations
        prune : bool
            If True, the algorithm sets small-activity dipoles to zero (pruning).
        pruning_thresh : float
            The threshold at which small gammas (dipole candidates) are set to
            zero.
        convergence_criterion : float
            Minimum change of loss function until convergence is assumed.
        
        Returns
        -------
        x : numpy.ndarray
            Parameter vector, e.g., source vector in the context of BSI (x in the cost
            function formula).
        
        '''
        n_chans, n_dipoles = self.leadfield.shape
        _, n_times = Y.shape
        L = deepcopy(self.leadfield)
        
        # re-reference data
        Y -= Y.mean(axis=0)

        # Scaling of the data (necessary for convergence criterion and pruning
        # threshold)
        Y_scaled = deepcopy(Y)
        scaler = abs(Y_scaled).mean()
        Y_scaled /= scaler

        # Random Initialization of Noise Covariance
        A = np.random.rand(n_chans, n_times)
        A =  (A@A.T)

        # MNE-based initialization of Gammas
        # gammas = np.ones(n_dipoles)
        lin_lstq = np.linalg.pinv(L) @ Y_scaled
        gammas = np.diagonal(lin_lstq@lin_lstq.T)
        gammas.setflags(write=True)
        Gamma = csr_matrix(np.diag(gammas))
        A.setflags(write=True)
        
        
        for i in range(max_iter):
            old_gammas = deepcopy(gammas)
            if i > 0:
                last_X_hat = deepcopy(X_hat)
            
            
            Sigma_y = A + L @ Gamma @ L.T
            Sigma_y_inv = np.linalg.inv(Sigma_y)
            X_hat = Gamma @ L.T @ Sigma_y_inv @ Y_scaled
            C_source = L.T @ Sigma_y_inv @ L
            M_source = (X_hat @ X_hat.T) / n_times
            
            # According to paper:
            # gammas = np.array([np.sqrt(M_source[n,n] / C_source[n, n]) for n in range(n_dipoles)])
            # gammas.setflags(write=True)

            # According to Code 1
            # https://github.com/AliHashemi-ai/FUN-Learning/blob/main/FUN_learning_cov_est.m
            # C_source_sqrt = np.real(sqrtm(C_source))
            # C_source_sqrt_inv = np.linalg.inv(C_source_sqrt)
            # gammas = np.diagonal(C_source_sqrt_inv @ np.real(sqrtm(C_source_sqrt @ M_source @ C_source_sqrt)) @ C_source_sqrt_inv)
            # gammas.setflags(write=True)
            # gammas[np.isnan(gammas)] = 0
            # Gamma = csr_matrix(np.diag(gammas))

            # According to code
            # https://github.com/AliHashemi-ai/FUN-Learning/blob/main/FUN_learning_cov_est.m
            Gamma = self.fun_learning_cov_est(C_source, M_source, update_mode="diagonal")
            gammas = np.diag(Gamma)
            gammas.setflags(write=True)
            gammas[np.isnan(gammas)] = 0
            
            
            # print("max gamma: ", gammas.max())
            if prune:
                prune_candidates = gammas<pruning_thresh
                # print(f"pruning {(prune_candidates).sum()}")
                gammas[prune_candidates] = 0
            Gamma = csr_matrix(np.diag(gammas))


            # Update Noise Part
            # According to Code:
            # https://github.com/AliHashemi-ai/FUN-Learning/blob/main/FUN_learning_cov_est.m
            Sigma_y = A + L @ Gamma @ L.T
            Sigma_y_inv = np.linalg.inv(Sigma_y)
            X_hat = Gamma @ L.T @ Sigma_y_inv @ Y_scaled
            E_total = Y_scaled - L @ X_hat
            M_noise = (E_total@E_total.T) / n_times
            M_noise.setflags(write=True)
            
            C_noise = Sigma_y_inv
            C_noise.setflags(write=True)
            
            A = self.fun_learning_cov_est(C_noise, M_noise, update_mode="diagonal")

            

            # Check if gammas went to zero
            if np.linalg.norm(gammas) == 0:
                # print("breaking")
                gammas = old_gammas
                break
            
            # if i>0 and np.sum((last_X_hat - X_hat)**2) < convergence_criterion:
            if i>0:
                dx = np.max(abs(last_X_hat - X_hat))
                # dx = np.sum((last_X_hat - X_hat)**2)
                print(dx)
                if dx < convergence_criterion:
                    # print("converged")
                    break

            
        # Final current source estimation
        # gammas /= gammas.max()
        Gamma = np.diag(gammas)
        Sigma_y = scaler*A + L @ Gamma @ L.T
        Sigma_y_inv = np.linalg.inv(Sigma_y)
        inverse_operator = Gamma @ L.T @ Sigma_y_inv
        
        # This is how the final source estimate could be calculated:
        # mu_x = inverse_operator @ Y


        return inverse_operator, scaler*A
    
    @staticmethod
    def fun_learning_cov_est(C, M, update_mode="diagonal"):
        # update_mode = 'Geodesic'; 
        # S = inv(sqrtm(C))*sqrtm((sqrtm(C))*M*(sqrtm(C)))*inv(sqrtm(C));
        if update_mode == "diagonal":
            h = np.diag(C)
            g = np.diag(M)    
            p = np.sqrt(g / h)
            S = np.diag(p)
        elif update_mode == "geodesic":
            # Efficient Implementation 
            # eps_default = 1e-5
            eps_default = 1e-8
            
            b_vec, b_val = np.linalg.eig(C)
            b_vec = np.diag(b_vec)
            root_C_coeff = np.sqrt(np.maximum(np.real(np.diagonal(b_val)), 0));

            inv_root_C_coeff = np.zeros(C.shape[0])
            inv_root_C_index = np.where(root_C_coeff >= eps_default)[0]
            
            inv_root_C_coeff[inv_root_C_index] = 1./root_C_coeff[inv_root_C_index]

            root_C = b_vec @ np.diag(root_C_coeff) @ b_vec.T
            inv_root_C = b_vec @ np.diag(inv_root_C_coeff) @ b_vec.T
            
            # [a_vec,a_val] = np.linalg.eig(root_C @ M @ root_C)
            [a_vec,a_val] = np.linalg.eig(inv_root_C @ M @ inv_root_C)
            a_vec = np.diag(a_vec)
            A_coeff = np.sqrt(np.maximum(np.real(np.diagonal(a_val)),0))
            A = a_vec @ np.diag(A_coeff) @ a_vec.T

            # S = inv_root_C @ A @ inv_root_C
            S = root_C @ A @ root_C
            
        else:
            msg = f"update_mode {update_mode} unknown"
            raise AttributeError(msg)

        return np.real(S)

class SolverHSChampagne(BaseSolver):
    ''' Class for the Homoscedastic Champagne (HS-Champagne) inverse solution. 

    References
    ----------
    [1] Hashemi, A., Cai, C., Gao, Y., Ghosh, S., Müller, K. R., Nagarajan, S.
    S., & Haufe, S. (2022). Joint learning of full-structure noise in
    hierarchical Bayesian regression models. IEEE Transactions on Medical
    Imaging.

    '''

    def __init__(self, name="HS-Champagne", **kwargs):
        self.name = name
        return super().__init__(**kwargs)

    def make_inverse_operator(self, forward, mne_obj, *args, alpha='auto', 
                              max_iter=1000, noise_cov=None, prune=False, 
                              pruning_thresh=1e-3, convergence_criterion=1e-8, 
                              return_noise_cov=False, update_noise=True, **kwargs):
        ''' Calculate inverse operator.

        Parameters
        ----------
        forward : mne.Forward
            The mne-python Forward model instance.
        mne_obj : [mne.Evoked, mne.Epochs, mne.io.Raw]
            The MNE data object.
        alpha : float
            The regularization parameter.
        max_iter : int
            Maximum number of iterations.
        noise_cov : [None, numpy.ndarray]
            The noise covariance matrix. Use "None" if not available.
        prune : bool
            If True, the algorithm sets small-activity dipoles to zero
            (pruning).
        pruning_thresh : float
            The threshold at which small gammas (dipole candidates) are set to
            zero.
        convergence_criterion : float
            Minimum change of loss function until convergence is assumed.
        
        Return
        ------
        self : object returns itself for convenience

        '''
        super().make_inverse_operator(forward, *args, alpha=alpha, **kwargs)
        data = self.unpack_data_obj(mne_obj)

        n_chans = self.leadfield.shape[0]
        if noise_cov is None:
            noise_cov = np.identity(n_chans)
        self.noise_cov = noise_cov
        inverse_operators = []
        inverse_operator, C_noise = self.make_hs_champagne(data, update_noise=update_noise, max_iter=max_iter, prune=prune, pruning_thresh=pruning_thresh, convergence_criterion=convergence_criterion)
        inverse_operators.append( inverse_operator )
        self.inverse_operators = [InverseOperator(inverse_operator, self.name) for inverse_operator in inverse_operators]
        if return_noise_cov:
            return C_noise
        else:
            return self
    
    def make_hs_champagne(self, Y, max_iter=1000, prune=False, 
                pruning_thresh=1e-3, convergence_criterion=1e-8,
                update_noise=True):
        ''' Homoscedastic Champagne method.

        Parameters
        ----------
        Y : array, shape (n_sensors,)
            measurement vector, capturing sensor measurements
        max_iter : int, optional
            The maximum number of inner loop iterations
        prune : bool
            If True, the algorithm sets small-activity dipoles to zero (pruning).
        pruning_thresh : float
            The threshold at which small gammas (dipole candidates) are set to
            zero.
        convergence_criterion : float
            Minimum change of loss function until convergence is assumed.
        Returns
        -------
        x : numpy.ndarray
            Parameter vector, e.g., source vector in the context of BSI (x in the cost
            function formula).
        
        '''
        import sys
        def is_singular(X):
            return np.linalg.cond(X) >= 1/sys.float_info.epsilon

        n_chans, n_dipoles = self.leadfield.shape
        _, n_times = Y.shape
        L = deepcopy(self.leadfield)
        
        # re-reference data
        Y -= Y.mean(axis=0)

        # Scaling of the data (necessary for convergence criterion and pruning
        # threshold)
        Y_scaled = deepcopy(Y)
        scaler = abs(Y_scaled).mean()
        Y_scaled /= scaler

        # Random Initialization of Noise Covariance
        A = np.random.rand(n_chans, n_times)
        A =  (A@A.T/n_times)
        # A = np.identity(n_chans)*0.001
        A.setflags(write=True)

        # MNE-based initialization of Gammas
        # gammas = np.ones(n_dipoles)
        lin_lstq = np.linalg.pinv(L) @ Y_scaled
        gammas = np.diagonal(lin_lstq@lin_lstq.T)
        gammas.setflags(write=True)
        Gamma = csr_matrix(np.diag(gammas))
        
        
        
        for i in range(max_iter):
            old_gammas = deepcopy(gammas)
            if i > 0:
                last_X_hat = deepcopy(X_hat)
            
            
            Sigma_y = A + L @ Gamma @ L.T
            Sigma_y_inv = np.linalg.inv(Sigma_y)
            Sigma_y_inv_L = Sigma_y_inv @ L
            X_hat = Gamma @ L.T @ Sigma_y_inv @ Y_scaled
            gammas_old  = deepcopy(gammas)

            S = Sigma_y_inv_L.T @ Y_scaled
            
            upper = np.mean(S**2,axis=1)
            lower = np.diag(L.T @ Sigma_y_inv_L)
            # print(upper.shape, lower.shape)
            gammas *= np.sqrt ( upper / lower)

            # gammas.setflags(write=True)
            gammas[np.isnan(gammas)] = 0
            

            # print("max gamma: ", gammas.max())
            if prune:
                prune_candidates = gammas<pruning_thresh
                # print(f"pruning {(prune_candidates).sum()}")
                gammas[prune_candidates] = 0
            
            Gamma_old  = deepcopy(Gamma)
            Gamma = csr_matrix(np.diag(gammas))

            # Check if gammas went to zero
            if np.linalg.norm(gammas) == 0:
                # print("breaking")
                gammas = old_gammas
                break

            # Update Noise Part
            if update_noise:
                if is_singular(A) or is_singular(Gamma_old.toarray()):
                    print(f"singular at {i}")
                    A = np.zeros((n_chans, n_chans))
                else:
                    E_total = Y_scaled - L @ X_hat
                    M_noise = (E_total@E_total.T) / n_times
                    # Expensive:
                    # Sigma_X = L.T @ np.linalg.inv(A) @ L + inv(Gamma_old)
                    # C_noise = (n_chans - n_dipoles + sum(np.diag(np.linalg.inv(Sigma_X)) /gammas_old))
                    # A = M_noise / C_noise
                    # Faster but less accurate:
                    Sigma_X_diag = gammas_old * (1 - gammas_old * np.diag(L.T @ Sigma_y_inv_L))
                    A = ( np.sum((Y_scaled - L @ X_hat)**2)**2 / n_times) / (n_chans - n_dipoles + sum(Sigma_X_diag / gammas_old))

                    A = np.identity(M_noise.shape[0]) * A
        
            
            
            # if i>0 and np.sum((last_X_hat - X_hat)**2) < convergence_criterion:
            if i>0:
                dx = np.max(abs(last_X_hat - X_hat))
                # dx = np.sum((last_X_hat - X_hat)**2)
                print(dx)
                if dx < convergence_criterion:
                    # print("converged")
                    break

            
        # Final current source estimation
        # gammas /= gammas.max()
        Gamma = np.diag(gammas)
        Sigma_y = scaler*A + L @ Gamma @ L.T
        Sigma_y_inv = np.linalg.inv(Sigma_y)
        inverse_operator = Gamma @ L.T @ Sigma_y_inv
        
        # This is how the final source estimate could be calculated:
        # mu_x = inverse_operator @ Y


        return inverse_operator, scaler*A
    

    @staticmethod
    def fun_learning_cov_est(C, M, update_mode="diagonal"):
        # update_mode = 'Geodesic'; 
        # S = inv(sqrtm(C))*sqrtm((sqrtm(C))*M*(sqrtm(C)))*inv(sqrtm(C));
        if update_mode == "diagonal":
            h = np.diag(C)
            g = np.diag(M)    
            p = np.sqrt(g / h)
            S = np.diag(p)
        elif update_mode == "geodesic":
            # Efficient Implementation 
            # eps_default = 1e-5
            eps_default = 1e-8
            
            b_vec, b_val = np.linalg.eig(C)
            b_vec = np.diag(b_vec)
            root_C_coeff = np.sqrt(np.maximum(np.real(np.diagonal(b_val)), 0));

            inv_root_C_coeff = np.zeros(C.shape[0])
            inv_root_C_index = np.where(root_C_coeff >= eps_default)[0]
            
            inv_root_C_coeff[inv_root_C_index] = 1./root_C_coeff[inv_root_C_index]

            root_C = b_vec @ np.diag(root_C_coeff) @ b_vec.T
            inv_root_C = b_vec @ np.diag(inv_root_C_coeff) @ b_vec.T
            
            # [a_vec,a_val] = np.linalg.eig(root_C @ M @ root_C)
            [a_vec,a_val] = np.linalg.eig(inv_root_C @ M @ inv_root_C)
            a_vec = np.diag(a_vec)
            A_coeff = np.sqrt(np.maximum(np.real(np.diagonal(a_val)),0))
            A = a_vec @ np.diag(A_coeff) @ a_vec.T

            # S = inv_root_C @ A @ inv_root_C
            S = root_C @ A @ root_C
            
        else:
            msg = f"update_mode {update_mode} unknown"
            raise AttributeError(msg)

        return np.real(S)
    

class SolverNLChampagne(BaseSolver):
    ''' Class for the Noise Learning Champagne (Champagne-NL) inverse solution. 

    References
    ----------
    [1] Cai, C., Hashemi, A., Diwakar, M., Haufe, S., Sekihara, K., & Nagarajan,
    S. S. (2021). Robust estimation of noise for electromagnetic brain imaging
    with the champagne algorithm. NeuroImage, 225, 117411.
    '''

    def __init__(self, name="Champagne-NL", **kwargs):
        self.name = name
        return super().__init__(**kwargs)

    def make_inverse_operator(self, forward, mne_obj, *args, alpha='auto', 
                              max_iter=1000, noise_cov=None, prune=True, 
                              pruning_thresh=1e-3, convergence_criterion=1e-8, 
                              **kwargs):
        ''' Calculate inverse operator.

        Parameters
        ----------
        forward : mne.Forward
            The mne-python Forward model instance.
        mne_obj : [mne.Evoked, mne.Epochs, mne.io.Raw]
            The MNE data object.
        alpha : float
            The regularization parameter.
        max_iter : int
            Maximum number of iterations.
        noise_cov : [None, numpy.ndarray]
            The noise covariance matrix. Use "None" if not available.
        prune : bool
            If True, the algorithm sets small-activity dipoles to zero
            (pruning).
        pruning_thresh : float
            The threshold at which small gammas (dipole candidates) are set to
            zero.
        convergence_criterion : float
            Minimum change of loss function until convergence is assumed.
        
        Return
        ------
        self : object returns itself for convenience

        '''
        super().make_inverse_operator(forward, *args, alpha=alpha, **kwargs)
        data = self.unpack_data_obj(mne_obj)

        n_chans = self.leadfield.shape[0]
        if noise_cov is None:
            noise_cov = np.identity(n_chans)
        self.noise_cov = noise_cov
        inverse_operators = []
        
        inverse_operator = self.nl_champagne(data, alpha=0.01, max_iter=max_iter, prune=prune, pruning_thresh=pruning_thresh, convergence_criterion=convergence_criterion)
        self.inverse_operators = [InverseOperator(inverse_operator, self.name)]
        return self
    
    def nl_champagne(self, Y, alpha=0.01, max_iter=1000, prune=True, 
                          pruning_thresh=1e-3, convergence_criterion=1e-8):
        ''' Noise Learning Champagne method.

        Parameters
        ----------
        Y : array, shape (n_sensors,)
            measurement vector, capturing sensor measurements
        alpha : float
            The regularization parameter.
        max_iter : int, optional
            The maximum number of inner loop iterations
        prune : bool
            If True, the algorithm sets small-activity dipoles to zero (pruning).
        pruning_thresh : float
            The threshold at which small gammas (dipole candidates) are set to
            zero.
        convergence_criterion : float
            Minimum change of loss function until convergence is assumed.
        Returns
        -------
        x : numpy.ndarray
            Parameter vector, e.g., source vector in the context of BSI (x in the cost
            function formula).
        
        '''
        n_chans, n_dipoles = self.leadfield.shape
        _, n_times = Y.shape
        L = deepcopy(self.leadfield)
        
        # re-reference data
        # Y -= Y.mean(axis=0)

        # Scaling of the data (necessary for convergence criterion and pruning
        # threshold)
        Y_scaled = deepcopy(Y)
        Y_scaled /= abs(Y_scaled).mean()

        I = np.identity(n_chans)
        
        alpha = np.random.rand(n_dipoles)
        Alpha = csr_matrix(np.diag(alpha))
        
        llambda = np.random.rand(n_chans)
        LLambda = csr_matrix(np.diag(llambda))

        
        # Sigma_y = L @ Alpha @ L.T + LLambda
        # Sigma_y_inv = np.linalg.inv(Sigma_y)

        # Sigma_x = Gamma - Gamma @ L.T @ Sigma_y_inv @ L @ Gamma
        # z_0 = L.T @ Sigma_y_inv @ L
        # mu_x = Gamma @ L.T @ Sigma_y_inv @ Y_scaled

        loss_list = [1e99,]
        
        for i in range(max_iter):
            previous_Alpha = deepcopy(Alpha)
            Sigma_y = L @ Alpha @ L.T + LLambda
            Sigma_y_inv = np.linalg.inv(Sigma_y)
            

            # 1) Alpha (formerly Gamma) update
            s_bar = np.squeeze(np.asarray(Alpha @ L.T @ Sigma_y_inv @ Y_scaled))
            z_hat = np.einsum('ij,ji->i', L.T @ Sigma_y_inv, L)
            C_s_bar = np.sum(s_bar**2, axis=1)
            
            alpha = np.sqrt( C_s_bar / z_hat )
            Alpha = csr_matrix(np.diag(alpha))

            # 2) LLambda update
            Y_hat = L @ s_bar

            ## Marginal likelihood maximazation updates
            # Gamma = inv(Alpha) + np.diag(L.T @ inv(LLambda) @ L)
            # Gamma_inv_L_T = np.linalg.solve(Gamma, L.T)
            # right_side = np.einsum('ij,ji->i', L, Gamma_inv_L_T)
            # llambda = np.sum((Y_scaled - Y_hat)**2, axis=1) + right_side

            ## Convex Bound update
            llambda = np.sqrt( np.sum((Y_scaled - Y_hat)**2, axis=1) / np.diag(Sigma_y_inv) )
            LLambda = csr_matrix(np.diag(llambda))

            # 3) Check convergence
            log_det_Sigma_y = np.log(np.linalg.det(Sigma_y))
            summation = (1 / n_times) * np.sum(np.einsum('ti,ij,tj->t', Y_scaled.T, Sigma_y_inv, Y_scaled.T))
            loss = log_det_Sigma_y + summation
            
            loss_list.append(loss)
            print(f"iter {i}: loss {loss:.2f} ({log_det_Sigma_y:.2f} + {(1 / n_times) * summation:.2f})")
            

            if loss == float("-inf") or loss == float("inf") or np.linalg.norm(alpha) == 0:
                Alpha = previous_Alpha
                break

            # if change < 0:
            #     Alpha = previous_Alpha
            #     print("Converged!")
            #     break

            change = abs(1 - ( loss_list[-1] / loss_list[-2]))
            print(change, loss_list[-1], loss_list[-2], convergence_criterion)
            if change < convergence_criterion:
                print("Converged!")
                break

            # change = loss_list[-2] - loss_list[-1] 
            # print(change, convergence_criterion*loss_list[-1])
            # if change < convergence_criterion*loss_list[-1]:
            #     print("Converged!")
            #     break

            if prune:
                prune_candidates = alpha<(pruning_thresh*alpha.max())
                alpha[prune_candidates] = 0
                Alpha = csr_matrix(np.diag(alpha))
                # print("pruned ", prune_candidates.sum(), " dipoles")
            
        # update rest
        Sigma_y = L @ Alpha @ L.T + LLambda
        Sigma_y_inv = np.linalg.inv(Sigma_y)
        inverse_operator = Alpha @ L.T @ Sigma_y_inv
        
        return inverse_operator